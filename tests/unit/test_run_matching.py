from datetime import UTC, date, datetime, time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from canonical.adapters.vn import VNMapping, vn_to_canonical
from canonical.domain import CanonicalTransaction
from reconciliation.adapters import CsvFixtureParser
from reconciliation.application import InvalidProviderStatement, RunMatching
from reconciliation.application.ports import RunClaim, StoredRun
from reconciliation.domain import ProviderTransaction
from shared_kernel import Region, TxnType
from shared_kernel.enums import Instrument


CSV_STATEMENT = b"""provider,region,tenant_id,provider_txn_id,provider_ref_id,merchant_ref,merchant_id,amount_minor,currency,status,txn_type,transaction_time
ONEPAY,VN,,onepay-payment-001,,system-001,merchant-001,149000,VND,SUCCESS,PAYMENT,2026-09-22T01:00:00+00:00
"""

CSV_WITH_NEXT_PERIOD_ROW = CSV_STATEMENT + (
    b"ONEPAY,VN,,next-period,,,merchant-001,100,VND,SUCCESS,PAYMENT,"
    b"2026-09-22T02:00:00+00:00\n"
)

CSV_HEADER = CSV_STATEMENT.splitlines(keepends=True)[0]


class RecordingRepository:
    def __init__(self) -> None:
        self.saved: dict[str, Any] | None = None
        self.job_run_id = UUID("00000000-0000-0000-0000-000000000002")
        self.existing: StoredRun | None = None
        self.failed: Exception | None = None
        self.claimed: dict[str, Any] | None = None
        self.late_rows: tuple[ProviderTransaction, ...] = ()
        self.late_request: dict[str, Any] | None = None

    @contextmanager
    def claim_run(self, **values: Any):
        self.claimed = values
        yield RunClaim(values["run_id"], self.job_run_id, self.existing)

    def complete_run(self, **values: Any) -> None:
        self.saved = values

    def late_provider_transactions(self, **values: Any):
        self.late_request = values
        return self.late_rows

    def fail_run(self, claim: RunClaim, error: Exception) -> None:
        self.failed = error


class StaticSystemTransactionSource:
    def __init__(self, rows: tuple[CanonicalTransaction, ...]) -> None:
        self.rows = rows
        self.request: tuple[Region, str | None, str, datetime, datetime] | None = None

    def get_transactions(
        self,
        region: Region,
        tenant_id: str | None,
        provider: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> tuple[CanonicalTransaction, ...]:
        self.request = (region, tenant_id, provider, start_utc, end_utc)
        return self.rows


class RecordingTelemetry:
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []
        self.dependencies: list[dict[str, Any]] = []

    def record_run(self, **values: Any) -> None:
        self.runs.append(values)

    def record_dependency(self, **values: Any) -> None:
        self.dependencies.append(values)


def _adapter_transaction(*, deleted: bool = False) -> CanonicalTransaction:
    return vn_to_canonical(
        {
            "_id": "system-deleted" if deleted else "system-001",
            "CreatedOn": "2026-09-22T01:00:00+00:00",
            "PaymentMethod": "CARD",
            "Status": 1,
            "Amount": "149000",
            "PaymentTransactionId": "unused" if deleted else "onepay-payment-001",
            "MerchantId": "merchant-001",
            "IsDeleted": deleted,
        },
        VNMapping(instrument_by_method={"CARD": Instrument.CARD}),
    )


def test_pipeline_accepts_adapter_output_without_loading_mongo(tmp_path: Path) -> None:
    statement = tmp_path / "onepay.csv"
    statement.write_bytes(CSV_STATEMENT)
    repository = RecordingRepository()
    source = StaticSystemTransactionSource(
        (_adapter_transaction(), _adapter_transaction(deleted=True))
    )
    telemetry = RecordingTelemetry()
    run_id = UUID("00000000-0000-0000-0000-000000000001")

    output = RunMatching(
        source,
        CsvFixtureParser(),
        repository,
        telemetry,
    ).execute(
        statement=statement,
        statement_content=statement.read_bytes(),
        business_date=date(2026, 9, 22),
        region=Region.VN,
        tenant_id=None,
        provider="onepay",
        business_timezone="UTC",
        cutoff_time=time(2),
        run_id=run_id,
    )

    assert output.run_id == run_id
    assert output.job_run_id == repository.job_run_id
    assert output.system_rows == 1
    assert output.provider_rows == 1
    assert output.summary == {"MATCHED": 1}
    assert not hasattr(output, "results")
    assert not hasattr(output, "financial_summary")
    assert source.request == (
        Region.VN, None, "ONEPAY",
        datetime(2026, 9, 21, 2, tzinfo=UTC),
        datetime(2026, 9, 22, 2, tzinfo=UTC),
    )
    assert repository.saved is not None
    assert repository.claimed is not None
    assert repository.claimed["provider"] == "ONEPAY"
    assert repository.claimed["tenant_id"] is None
    assert len(repository.saved["results"]) == 1
    assert repository.saved["financial_summary"]["totals_by_currency"][0] == {
        "currency": "VND",
        "system_amount_minor": 149000,
        "provider_amount_minor": 149000,
        "delta_amount_minor": 0,
        "system_amount_count": 1,
        "provider_amount_count": 1,
        "system_missing_amount_count": 0,
        "provider_missing_amount_count": 0,
    }
    assert telemetry.runs[0]["outcome"] == "completed"
    assert telemetry.runs[0]["run_id"] == run_id
    assert telemetry.runs[0]["business_date"] == date(2026, 9, 22)
    assert telemetry.runs[0]["currency_deltas"] == {"VND": 0}
    assert {
        (item["dependency"], item["operation"], item["outcome"])
        for item in telemetry.dependencies
    } == {
        ("repository", "late_provider_transactions", "success"),
        ("system_source", "get_transactions", "success"),
        ("repository", "complete_run", "success"),
    }
    repository.existing = StoredRun(
        output.system_rows,
        output.provider_rows,
        output.summary,
        tuple(repository.saved["results"]),
    )
    replay = RunMatching(source, CsvFixtureParser(), repository).execute(
        statement=statement, statement_content=b"skip parser on replay",
        business_date=date(2026, 9, 22), region=Region.VN,
        tenant_id=None, provider="onepay", business_timezone="UTC",
        cutoff_time=time(2), run_id=run_id,
    )
    assert replay.replayed is True
    assert replay.summary == output.summary


def test_data_quality_failure_does_not_change_committed_output(
    tmp_path: Path,
) -> None:
    repository = RecordingRepository()
    calls = []

    def failing_data_quality(run_id: UUID) -> None:
        calls.append((run_id, repository.saved is not None))
        raise RuntimeError("DQ is unavailable")

    output = RunMatching(
        StaticSystemTransactionSource((_adapter_transaction(),)),
        CsvFixtureParser(),
        repository,
        data_quality=failing_data_quality,
    ).execute(
        statement=tmp_path / "onepay.csv",
        statement_content=CSV_STATEMENT,
        business_date=date(2026, 9, 22),
        region=Region.VN,
        tenant_id=None,
        provider="ONEPAY",
        business_timezone="UTC",
        cutoff_time=time(2),
        run_id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    assert output.summary == {"MATCHED": 1}
    assert calls == [(output.run_id, True)]


def test_completed_replay_skips_parser_source_and_matching(tmp_path: Path) -> None:
    repository = RecordingRepository()
    repository.existing = StoredRun(4, 2, {"MATCHED": 2}, ())
    source = StaticSystemTransactionSource(())
    run_id = UUID("00000000-0000-0000-0000-000000000001")

    output = RunMatching(source, CsvFixtureParser(), repository).execute(
        statement=tmp_path / "old.csv", statement_content=b"not even valid CSV",
        business_date=date(2026, 9, 22), region=Region.VN,
        tenant_id=None, provider="onepay", business_timezone="UTC",
        cutoff_time=time(2), run_id=run_id,
    )

    assert output.run_id == run_id
    assert output.replayed is True
    assert output.summary == {"MATCHED": 2}
    assert output.system_rows == 4
    assert source.request is None
    assert repository.saved is None


def test_row_at_cutoff_is_persisted_but_not_matched_in_current_run(
    tmp_path: Path,
) -> None:
    repository = RecordingRepository()
    run_id = UUID("00000000-0000-0000-0000-000000000001")

    output = RunMatching(
        StaticSystemTransactionSource((_adapter_transaction(),)),
        CsvFixtureParser(),
        repository,
    ).execute(
        statement=tmp_path / "onepay.csv",
        statement_content=CSV_WITH_NEXT_PERIOD_ROW,
        business_date=date(2026, 9, 22),
        region=Region.VN,
        tenant_id=None,
        provider="ONEPAY",
        business_timezone="UTC",
        cutoff_time=time(2),
        run_id=run_id,
    )

    assert output.provider_rows == 1
    assert output.summary == {"MATCHED": 1}
    assert repository.saved is not None
    persisted = repository.saved["provider_txns"]
    assert len(persisted) == 2
    assert persisted[0].late_arriving is False
    assert persisted[1].late_arriving is True


def test_late_row_is_carried_into_its_business_period(tmp_path: Path) -> None:
    repository = RecordingRepository()
    repository.late_rows = (ProviderTransaction(
        provider="ONEPAY", region=Region.VN, tenant_id=None,
        provider_txn_id="next-period", provider_ref_id=None,
        merchant_ref=None, merchant_id="merchant-001", amount_minor=100,
        currency="VND", status="SUCCESS", txn_type=TxnType.PAYMENT,
        transaction_time=datetime(2026, 9, 22, 2, tzinfo=UTC),
        source_file="onepay-20260922.csv", source_row_number=3,
        late_arriving=True,
    ),)
    source = StaticSystemTransactionSource((vn_to_canonical(
        {
            "_id": "system-next-period",
            "CreatedOn": "2026-09-22T02:00:00+00:00",
            "PaymentMethod": "CARD", "Status": 1, "Amount": "100",
            "PaymentTransactionId": "next-period",
            "MerchantId": "merchant-001",
        },
        VNMapping(instrument_by_method={"CARD": Instrument.CARD}),
    ),))

    output = RunMatching(source, CsvFixtureParser(), repository).execute(
        statement=tmp_path / "onepay-20260923.csv",
        statement_content=CSV_HEADER,
        business_date=date(2026, 9, 23), region=Region.VN,
        tenant_id=None, provider="ONEPAY", business_timezone="UTC",
        cutoff_time=time(2), run_id=UUID(int=1),
    )

    assert output.provider_rows == 1
    assert output.summary == {"MATCHED": 1}
    assert repository.late_request == {
        "business_date": date(2026, 9, 23),
        "region": Region.VN,
        "tenant_id": None,
        "provider": "ONEPAY",
        "start_utc": datetime(2026, 9, 22, 2, tzinfo=UTC),
        "end_utc": datetime(2026, 9, 23, 2, tzinfo=UTC),
    }
    assert repository.saved is not None
    assert repository.saved["provider_txns"] == list(repository.late_rows)


def test_current_file_wins_when_it_repeats_a_carried_row(tmp_path: Path) -> None:
    repository = RecordingRepository()
    repository.late_rows = (ProviderTransaction(
        provider="ONEPAY", region=Region.VN, tenant_id=None,
        provider_txn_id="onepay-payment-001", provider_ref_id=None,
        merchant_ref="system-001", merchant_id="merchant-001",
        amount_minor=149000, currency="VND", status="SUCCESS",
        txn_type=TxnType.PAYMENT,
        transaction_time=datetime(2026, 9, 22, 1, tzinfo=UTC),
        source_file="old.csv", source_row_number=2, late_arriving=True,
    ),)

    output = RunMatching(
        StaticSystemTransactionSource((_adapter_transaction(),)),
        CsvFixtureParser(), repository,
    ).execute(
        statement=tmp_path / "new.csv", statement_content=CSV_STATEMENT,
        business_date=date(2026, 9, 22), region=Region.VN,
        tenant_id=None, provider="ONEPAY", business_timezone="UTC",
        cutoff_time=time(2), run_id=UUID(int=1),
    )

    assert output.provider_rows == 1
    assert output.summary == {"MATCHED": 1}
    assert repository.saved is not None
    assert [row.source_file for row in repository.saved["provider_txns"]] == [
        "new.csv"
    ]


def test_validation_failure_is_recorded_after_claim(tmp_path: Path) -> None:
    repository = RecordingRepository()
    run_id = UUID("00000000-0000-0000-0000-000000000001")
    with pytest.raises(InvalidProviderStatement):
        RunMatching(StaticSystemTransactionSource(()), CsvFixtureParser(), repository).execute(
            statement=tmp_path / "bad.csv", statement_content=b"bad csv",
            business_date=date(2026, 9, 22), region=Region.VN,
            tenant_id=None, provider="ONEPAY", business_timezone="UTC",
            cutoff_time=time(2), run_id=run_id,
        )
    assert isinstance(repository.failed, InvalidProviderStatement)
