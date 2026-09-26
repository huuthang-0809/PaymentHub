from datetime import date
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
from shared_kernel import Region
from shared_kernel.enums import Instrument


CSV_STATEMENT = b"""provider,region,tenant_id,provider_txn_id,provider_ref_id,merchant_ref,merchant_id,amount_minor,currency,status,txn_type,transaction_time
ONEPAY,VN,,onepay-payment-001,,system-001,merchant-001,149000,VND,SUCCESS,PAYMENT,2026-09-22T01:00:00+00:00
"""


class RecordingRepository:
    def __init__(self) -> None:
        self.saved: dict[str, Any] | None = None
        self.job_run_id = UUID("00000000-0000-0000-0000-000000000002")
        self.existing: StoredRun | None = None
        self.failed: Exception | None = None
        self.claimed: dict[str, Any] | None = None

    @contextmanager
    def claim_run(self, **values: Any):
        self.claimed = values
        yield RunClaim(values["run_id"], self.job_run_id, self.existing)

    def complete_run(self, **values: Any) -> None:
        self.saved = values

    def fail_run(self, claim: RunClaim, error: Exception) -> None:
        self.failed = error


class StaticSystemTransactionSource:
    def __init__(self, rows: tuple[CanonicalTransaction, ...]) -> None:
        self.rows = rows
        self.request: tuple[Region, str | None, str, date] | None = None

    def get_transactions(
        self,
        region: Region,
        tenant_id: str | None,
        provider: str,
        business_date: date,
    ) -> tuple[CanonicalTransaction, ...]:
        self.request = (region, tenant_id, provider, business_date)
        return self.rows


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
    run_id = UUID("00000000-0000-0000-0000-000000000001")

    output = RunMatching(
        source,
        CsvFixtureParser(),
        repository,
    ).execute(
        statement=statement,
        statement_content=statement.read_bytes(),
        business_date=date(2026, 9, 22),
        region=Region.VN,
        tenant_id=None,
        provider="onepay",
        run_id=run_id,
    )

    assert output.run_id == run_id
    assert output.job_run_id == repository.job_run_id
    assert output.system_rows == 1
    assert output.provider_rows == 1
    assert output.summary == {"MATCHED": 1}
    assert source.request == (Region.VN, None, "ONEPAY", date(2026, 9, 22))
    assert repository.saved is not None
    assert repository.claimed is not None
    assert repository.claimed["provider"] == "ONEPAY"
    assert repository.claimed["tenant_id"] is None
    assert len(repository.saved["results"]) == 1


def test_completed_replay_skips_parser_source_and_matching(tmp_path: Path) -> None:
    repository = RecordingRepository()
    repository.existing = StoredRun(4, 2, {"MATCHED": 2}, ())
    source = StaticSystemTransactionSource(())
    run_id = UUID("00000000-0000-0000-0000-000000000001")

    output = RunMatching(source, CsvFixtureParser(), repository).execute(
        statement=tmp_path / "old.csv", statement_content=b"not even valid CSV",
        business_date=date(2026, 9, 22), region=Region.VN,
        tenant_id=None, provider="onepay", run_id=run_id,
    )

    assert output.run_id == run_id
    assert output.summary == {"MATCHED": 2}
    assert output.system_rows == 4
    assert source.request is None
    assert repository.saved is None


def test_validation_failure_is_recorded_after_claim(tmp_path: Path) -> None:
    repository = RecordingRepository()
    run_id = UUID("00000000-0000-0000-0000-000000000001")
    with pytest.raises(InvalidProviderStatement):
        RunMatching(StaticSystemTransactionSource(()), CsvFixtureParser(), repository).execute(
            statement=tmp_path / "bad.csv", statement_content=b"bad csv",
            business_date=date(2026, 9, 22), region=Region.VN,
            tenant_id=None, provider="ONEPAY", run_id=run_id,
        )
    assert isinstance(repository.failed, InvalidProviderStatement)
