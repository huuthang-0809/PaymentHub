from datetime import UTC, datetime

from canonical.domain import CanonicalTransaction
from reconciliation.adapters import CsvFixtureParser
from reconciliation.domain import MatchingEngine, ReconResultType
from shared_kernel import AmountStatus, CanonicalStatus, Region, TxnType

CSV_FIXTURE = """provider,region,tenant_id,provider_txn_id,provider_ref_id,merchant_ref,merchant_id,amount_minor,currency,status,txn_type,transaction_time
ONEPAY,VN,,provider-1,,merchant-ref-1,merchant-1,10000,VND,SUCCESS,PAYMENT,2026-09-22T08:00:00+00:00
ONEPAY,VN,,provider-2,,merchant-ref-2,merchant-1,9000,VND,SUCCESS,PAYMENT,2026-09-22T08:01:00+00:00
ONEPAY,VN,,provider-only,,merchant-ref-3,merchant-1,5000,VND,SUCCESS,PAYMENT,2026-09-22T08:02:00+00:00
"""


def system_transaction(
    txn_id: str, provider_txn_id: str, amount_minor: int, minute: int
) -> CanonicalTransaction:
    return CanonicalTransaction(
        region=Region.VN,
        tenant_id=None,
        txn_id=txn_id,
        txn_type=TxnType.PAYMENT,
        provider="ONEPAY",
        status=CanonicalStatus.SUCCESS,
        amount_status=AmountStatus.OK,
        created_at=datetime(2026, 9, 22, 8, minute, tzinfo=UTC),
        is_terminal=True,
        is_money_movement=True,
        is_reconcilable=True,
        provider_txn_id=provider_txn_id,
        merchant_id="merchant-1",
        amount_minor=amount_minor,
        currency="VND",
    )


def test_csv_fixture_can_feed_matching_engine() -> None:
    report = CsvFixtureParser().parse(CSV_FIXTURE.encode(), "provider_statement.csv")
    assert report.valid
    assert len(report.rows) == 3
    assert all(row.raw == {} for row in report.rows)
    raw_rows = list(report.raw_rows or ())
    assert raw_rows[0]["provider_txn_id"] == "provider-1"
    assert raw_rows[0]["amount_minor"] == "10000"

    system_rows = [
        system_transaction("system-1", "provider-1", 10_000, 0),
        system_transaction("system-2", "provider-2", 10_000, 1),
        system_transaction("system-only", "missing-provider", 7_000, 3),
    ]
    results = MatchingEngine().match("run-1", list(report.rows), system_rows)

    assert [result.result_type for result in results] == [
        ReconResultType.MATCHED,
        ReconResultType.AMOUNT_MISMATCH,
        ReconResultType.MISSING_IN_PROVIDER,
        ReconResultType.MISSING_IN_SYSTEM,
    ]
    report.close()


def test_csv_fixture_reports_validation_errors() -> None:
    content = CSV_FIXTURE.splitlines()
    bad_csv = "\n".join(
        [
            content[0],
            content[1].replace(",10000,VND,", ",-1,VND,"),
            content[1].replace(",VND,", ",BTC,"),
            content[1].replace("2026-09-22T08:00:00+00:00", "not-a-date"),
            content[1],
            content[1],
            ",,,,,,,,,,,",
        ]
    ).encode()

    report = CsvFixtureParser().parse(bad_csv, "bad.csv")

    assert {issue.code.value for issue in report.issues} == {
        "NEGATIVE_AMOUNT",
        "UNKNOWN_CURRENCY",
        "INVALID_DATE",
        "DUPLICATE_ROW",
        "EMPTY_ROW",
    }
