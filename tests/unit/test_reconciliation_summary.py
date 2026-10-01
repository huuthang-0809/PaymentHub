from dataclasses import replace
from decimal import Decimal

import pytest

from reconciliation.application.summary import build_financial_summary
from reconciliation.domain import ReconResult, ReconResultType as Result
from shared_kernel import CanonicalStatus as Status, Region, TxnType


def payment(**changes):
    row = ReconResult(
        run_id="run", result_type=Result.MATCHED, region=Region.INDIA,
        tenant_id=None, provider="RAZORPAY", txn_type=TxnType.PAYMENT,
        system_txn_id="parent", system_provider_txn_id="pay-1",
        system_status=Status.PARTIAL_REFUNDED, system_amount_minor=100,
        system_currency="INR", refunded_amount_minor=30,
        provider_txn_id="pay-1", provider_amount_minor=100,
        provider_currency="INR", source_row_number=2, delta_amount=0,
    )
    return replace(row, **changes)


def refund(**changes):
    row = payment(
        txn_type=TxnType.REFUND, system_txn_id="refund",
        system_provider_txn_id="refund-1", system_status=Status.SUCCESS,
        system_amount_minor=30, provider_amount_minor=30,
        provider_txn_id="refund-1", source_row_number=3,
        refund_of="pay-1", refunded_amount_minor=None,
    )
    return replace(row, **changes)


def test_totals_include_missing_and_currency_mismatch_without_cross_currency_sum():
    rows = [
        payment(),
        payment(result_type=Result.MISSING_IN_PROVIDER, system_txn_id="missing",
                system_amount_minor=50, provider_amount_minor=None,
                provider_currency=None, source_row_number=None, delta_amount=None),
        payment(result_type=Result.MISSING_IN_SYSTEM, system_txn_id=None,
                system_amount_minor=None, system_currency=None,
                provider_amount_minor=20, delta_amount=None),
        payment(result_type=Result.CURRENCY_MISMATCH, system_txn_id="currency",
                system_amount_minor=10, provider_amount_minor=3,
                provider_currency="USD", delta_amount=None),
    ]
    totals = build_financial_summary(rows).totals_by_currency
    assert [(t.currency, t.system_amount_minor, t.provider_amount_minor,
             t.delta_amount_minor) for t in totals] == [
        ("INR", 160, 120, 40), ("USD", None, 3, -3),
    ]


def test_large_database_decimal_amounts_remain_exact_in_minor_units():
    amount = Decimal("12345678901234567890123456789012345678")
    totals = build_financial_summary([
        payment(system_amount_minor=amount, provider_amount_minor=int(amount) - 1),
    ]).totals_by_currency[0]
    assert totals.system_amount_minor == int(amount)
    assert totals.delta_amount_minor == 1


@pytest.mark.parametrize("amount", [None, 999])
def test_invalid_amount_is_not_added_or_treated_as_zero(amount):
    rows = [payment(), payment(
        result_type=Result.INVALID_DATA, system_txn_id="invalid",
        system_amount_minor=amount, provider_amount_minor=None,
        provider_currency=None, source_row_number=None,
    )]
    totals = build_financial_summary(rows).totals_by_currency[0]
    assert totals.system_amount_minor == 100
    assert totals.system_missing_amount_count == 1
    assert totals.delta_amount_minor is None


def test_missing_currency_prevents_a_complete_delta():
    summary = build_financial_summary([payment(), payment(
        result_type=Result.INVALID_DATA, system_txn_id="no-currency",
        system_currency=None, system_amount_minor=None,
        provider_amount_minor=None, provider_currency=None, source_row_number=None,
    )])
    assert summary.unallocated_currency_count == 1
    assert summary.totals_by_currency[0].delta_amount_minor is None


def test_duplicates_and_not_reconcilable_do_not_affect_any_financial_metrics():
    primary = [payment(), refund()]
    extra = [
        refund(result_type=Result.DUPLICATE),
        payment(result_type=Result.DUPLICATE, refunded_amount_minor=999),
        payment(result_type=Result.NOT_RECONCILABLE, txn_type=TxnType.CARD_LINK),
    ]
    summary = build_financial_summary(primary + extra)
    baseline = build_financial_summary(primary)
    assert replace(summary, breakdowns=baseline.breakdowns) == baseline
    excluded = [b for b in summary.breakdowns
                if b.result in {"DUPLICATE", "NOT_RECONCILABLE"}]
    assert sum(b.system_count for b in excluded) == 3
    assert all(not b.totals_by_currency for b in excluded)
    assert summary.totals_by_currency[0].system_amount_minor == 130
    assert summary.refund_cross_checks[0].successful_child_amount_minor == 30
    assert summary.refund_cross_checks[0].status == "MATCHED"


def test_parent_rate_uses_status_or_counter_once_per_parent_not_child_count():
    rows = [
        payment(),
        payment(system_txn_id="load", system_provider_txn_id="load-1",
                txn_type=TxnType.LOAD, system_status=Status.SUCCESS),
        payment(system_txn_id="failed", system_provider_txn_id="pay-2",
                system_status=Status.FAILED, refunded_amount_minor=0),
        refund(system_amount_minor=10),
        refund(system_txn_id="refund-2", system_amount_minor=20),
    ]
    metrics = build_financial_summary(rows).refund_parent_metrics
    assert metrics.eligible_parent_count == 3
    assert metrics.refunded_parent_count == 2
    assert metrics.refunded_transaction_rate == "0.666667"
    assert metrics.missing_evidence_count == 0


def test_zero_denominator_and_legacy_missing_evidence_return_null_rate(): #week 3 test
    assert build_financial_summary([]).refund_parent_metrics.refunded_transaction_rate is None
    metrics = build_financial_summary([
        payment(system_status=None, refunded_amount_minor=None),
    ]).refund_parent_metrics
    assert metrics.eligible_parent_count == 1
    assert metrics.missing_evidence_count == 1
    assert metrics.refunded_transaction_rate is None


def test_vn_links_by_internal_id_and_only_sums_successful_children():
    rows = [
        payment(region=Region.VN, refunded_amount_minor=None),
        refund(region=Region.VN, refund_of="parent"),
        refund(region=Region.VN, refund_of="parent", system_txn_id="failed-refund",
               system_status=Status.FAILED, system_amount_minor=50),
    ]
    check = build_financial_summary(rows).refund_cross_checks[0]
    assert check.status == "LINKED"
    assert check.successful_child_count == 1
    assert check.successful_child_amount_minor == 30


@pytest.mark.parametrize(("parent_changes", "child_changes", "expected"), [
    ({"refunded_amount_minor": 40}, {}, "AMOUNT_MISMATCH"),
    ({"refunded_amount_minor": None}, {}, "PARENT_TOTAL_UNAVAILABLE"),
    ({}, {"refund_of": None}, "MISSING_PARENT_REFERENCE"),
    ({"provider": "OTHER"}, {}, "PARENT_NOT_IN_SNAPSHOT"),
    ({"region": Region.VN}, {}, "PARENT_NOT_IN_SNAPSHOT"),
    ({"tenant_id": "other"}, {}, "PARENT_NOT_IN_SNAPSHOT"),
    ({}, {"system_currency": "USD"}, "CURRENCY_MISMATCH"),
    ({}, {"system_currency": None}, "MISSING_CURRENCY"),
    ({}, {"system_amount_minor": None}, "INCOMPLETE_CHILD_DATA"),
    ({}, {"system_status": Status.UNKNOWN}, "INCOMPLETE_CHILD_DATA"),
])
def test_refund_cross_check_reports_missing_or_conflicting_evidence(
    parent_changes, child_changes, expected,
):
    summary = build_financial_summary([payment(**parent_changes), refund(**child_changes)])
    check = next(c for c in summary.refund_cross_checks if c.successful_child_count
                 or c.status == "INCOMPLETE_CHILD_DATA")
    assert check.status == expected


def test_refunded_parent_without_children_and_ambiguous_parent_are_visible():
    assert build_financial_summary([payment()]).refund_cross_checks[0].status == "CHILDREN_NOT_IN_SNAPSHOT"
    checks = build_financial_summary([
        payment(), payment(system_txn_id="another-parent"), refund(),
    ]).refund_cross_checks
    assert checks[0].status == "AMBIGUOUS_PARENT"


def test_breakdowns_count_each_side_and_keep_currency_totals_separate():
    rows = [
        payment(), refund(),
        payment(result_type=Result.MISSING_IN_PROVIDER, source_row_number=None,
                provider_amount_minor=None, provider_currency=None),
        payment(result_type=Result.MISSING_IN_SYSTEM, system_txn_id=None,
                system_amount_minor=None, system_currency=None, txn_type=None),
        payment(result_type=Result.CURRENCY_MISMATCH, provider_currency="USD"),
        payment(region=Region.ASIA, tenant_id="asia-id", provider="ESPAY"),
        payment(region=Region.ASIA, tenant_id="asia-ph", provider="ESPAY"),
        payment(region=Region.VN, provider="ONEPAY"),
        payment(result_type=Result.INVALID_DATA, system_amount_minor=None,
                source_row_number=None, provider_amount_minor=None, provider_currency=None),
    ]
    summary = build_financial_summary(rows)
    assert summary == build_financial_summary(reversed(rows))
    assert len(summary.breakdowns) == 9
    assert sum(b.system_count for b in summary.breakdowns) == 8
    assert sum(b.provider_count for b in summary.breakdowns) == 7
    mismatch = next(b for b in summary.breakdowns if b.result == "CURRENCY_MISMATCH")
    assert [t.currency for t in mismatch.totals_by_currency] == ["INR", "USD"]
    invalid = next(b for b in summary.breakdowns if b.result == "INVALID_DATA")
    assert invalid.system_count == 1
    assert invalid.totals_by_currency[0].system_missing_amount_count == 1


def test_refund_totals_use_successful_system_children_and_expose_missing_data():
    rows = [payment(refunded_amount_minor=999), refund(),
            refund(system_status=Status.PARTIAL_REFUNDED, system_amount_minor=20),
            refund(system_status=Status.REFUNDED, system_amount_minor=10),
            refund(system_status=Status.PENDING), refund(system_status=Status.FAILED),
            refund(system_status=Status.UNKNOWN), refund(system_txn_id=None),
            refund(result_type=Result.DUPLICATE),
            refund(result_type=Result.NOT_RECONCILABLE),
            refund(system_amount_minor=None),
            refund(result_type=Result.INVALID_DATA, system_amount_minor=999),
            refund(region=Region.ASIA, system_currency="USD", system_amount_minor=5),
            refund(system_currency=None),
            refund(system_currency="VND", system_amount_minor=None)]
    totals = {t.currency: t for t in build_financial_summary(rows).refunds_by_currency}
    assert (totals["INR"].count, totals["INR"].amount_minor,
            totals["INR"].missing_amount_count) == (5, 60, 2)
    assert totals["USD"].amount_minor == 5
    assert totals[None].count == 1
    assert totals[None].amount_minor is None
    assert totals["VND"].amount_minor is None
    assert build_financial_summary([]).refunds_by_currency == ()
    assert build_financial_summary([]).breakdowns == ()
