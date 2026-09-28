"""Financial summary rebuilt from persisted reconciliation results."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from reconciliation.domain import ReconResult, ReconResultType
from shared_kernel import CanonicalStatus, Region, TxnType


REFUNDED_STATUSES = {CanonicalStatus.REFUNDED, CanonicalStatus.PARTIAL_REFUNDED}
SUCCESSFUL_REFUND_STATUSES = REFUNDED_STATUSES | {CanonicalStatus.SUCCESS}


@dataclass(frozen=True)
class CurrencyTotals:
    currency: str
    system_amount_minor: int | None
    provider_amount_minor: int | None
    delta_amount_minor: int | None
    system_amount_count: int
    provider_amount_count: int
    system_missing_amount_count: int
    provider_missing_amount_count: int


@dataclass(frozen=True)
class RefundParentMetrics:
    eligible_parent_count: int
    refunded_parent_count: int
    missing_evidence_count: int
    refunded_transaction_rate: str | None


@dataclass(frozen=True)
class RefundCrossCheck:
    region: str
    tenant_id: str | None
    provider: str
    refund_of: str | None
    currency: str | None
    successful_child_count: int
    successful_child_amount_minor: int | None
    parent_refunded_amount_minor: int | None
    status: str


@dataclass(frozen=True)
class FinancialSummary:
    totals_by_currency: tuple[CurrencyTotals, ...]
    unallocated_currency_count: int
    refund_parent_metrics: RefundParentMetrics
    refund_cross_checks: tuple[RefundCrossCheck, ...]


def build_financial_summary(results: Iterable[ReconResult]) -> FinancialSummary:
    rows = tuple(row for row in results if row.result_type not in {
        ReconResultType.DUPLICATE, ReconResultType.NOT_RECONCILABLE,
    })
    amounts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    unallocated = 0
    for row in rows:
        for side, present in (
            ("system", row.system_txn_id is not None),
            ("provider", row.source_row_number is not None),
        ):
            amount = getattr(row, f"{side}_amount_minor")
            currency = getattr(row, f"{side}_currency")
            if not present and amount is None and currency is None:
                continue
            if not currency:
                unallocated += 1
                continue
            bucket = amounts[currency]
            if amount is None or (
                side == "system" and row.result_type is ReconResultType.INVALID_DATA
            ):
                bucket[f"{side}_missing"] += 1
            else:
                bucket[side] += int(amount)
                bucket[f"{side}_count"] += 1

    totals = tuple(
        CurrencyTotals(
            currency=currency,
            system_amount_minor=bucket["system"] if bucket["system_count"] else None,
            provider_amount_minor=bucket["provider"] if bucket["provider_count"] else None,
            # An absent counterpart contributes an empty sum. An unknown amount
            # or currency makes the full-run difference unavailable.
            delta_amount_minor=(
                bucket["system"] - bucket["provider"]
                if not (bucket["system_missing"] or bucket["provider_missing"] or unallocated)
                else None
            ),
            system_amount_count=bucket["system_count"],
            provider_amount_count=bucket["provider_count"],
            system_missing_amount_count=bucket["system_missing"],
            provider_missing_amount_count=bucket["provider_missing"],
        )
        for currency, bucket in sorted(amounts.items())
    )
    parents = tuple(row for row in rows if row.system_txn_id and row.txn_type in {
        TxnType.PAYMENT, TxnType.LOAD,
    })
    refunded = sum(_is_refunded(parent) for parent in parents)
    missing = sum(
        parent.system_status in {None, CanonicalStatus.UNKNOWN}
        and not _is_refunded(parent)
        for parent in parents
    )
    rate = (
        format(Decimal(refunded) / Decimal(len(parents)), ".6f")
        if parents and not missing else None
    )
    return FinancialSummary(
        totals, unallocated,
        RefundParentMetrics(len(parents), refunded, missing, rate),
        _refund_cross_checks(rows, parents),
    )


def _is_refunded(parent: ReconResult) -> bool:
    return parent.system_status in REFUNDED_STATUSES or (
        parent.refunded_amount_minor is not None and parent.refunded_amount_minor > 0
    )


def _refund_cross_checks(
    rows: tuple[ReconResult, ...], parents: tuple[ReconResult, ...],
) -> tuple[RefundCrossCheck, ...]:
    parent_index: dict[tuple, list[ReconResult]] = defaultdict(list)
    groups: dict[tuple, list[ReconResult]] = defaultdict(list)
    for parent in parents:
        if parent.region not in {Region.VN, Region.INDIA}:
            continue
        reference = (
            parent.system_txn_id if parent.region is Region.VN
            else parent.system_provider_txn_id
        )
        if reference:
            key = (parent.region, parent.tenant_id, parent.provider, reference)
            parent_index[key].append(parent)
            if _is_refunded(parent):
                groups[(*key, parent.system_currency)]  # Report absent children too.
    for row in rows:
        if row.system_txn_id and row.txn_type is TxnType.REFUND and row.region in {
            Region.VN, Region.INDIA,
        }:
            groups[(row.region, row.tenant_id, row.provider,
                    row.refund_of, row.system_currency)].append(row)

    checks = []
    for key, children in sorted(groups.items(), key=lambda item: str(item[0])):
        region, tenant, provider, reference, currency = key
        candidates = parent_index[key[:4]] if reference else []
        parent = candidates[0] if len(candidates) == 1 else None
        successful = [row for row in children if row.system_status in SUCCESSFUL_REFUND_STATUSES]
        incomplete = any(
            row.system_status in {None, CanonicalStatus.UNKNOWN}
            or (row.system_status in SUCCESSFUL_REFUND_STATUSES and (
                row.system_amount_minor is None or row.result_type is ReconResultType.INVALID_DATA
            ))
            for row in children
        )
        child_amount = (
            sum(int(row.system_amount_minor) for row in successful)
            if children and not incomplete and currency else None
        )
        parent_amount = (
            int(parent.refunded_amount_minor)
            if parent and parent.refunded_amount_minor is not None else None
        )
        if not reference:
            status = "MISSING_PARENT_REFERENCE"
        elif len(candidates) > 1:
            status = "AMBIGUOUS_PARENT"
        elif parent is None:
            status = "PARENT_NOT_IN_SNAPSHOT"
        elif currency is None or parent.system_currency is None:
            status = "MISSING_CURRENCY"
        elif parent.system_currency != currency:
            status = "CURRENCY_MISMATCH"
        elif not children:
            status = "CHILDREN_NOT_IN_SNAPSHOT"
        elif incomplete:
            status = "INCOMPLETE_CHILD_DATA"
        elif region is Region.VN:
            status = "LINKED"
        elif parent_amount is None:
            status = "PARENT_TOTAL_UNAVAILABLE"
        else:
            status = "MATCHED" if child_amount == parent_amount else "AMOUNT_MISMATCH"
        checks.append(RefundCrossCheck(
            region.value, tenant, provider, reference, currency,
            len(successful), child_amount, parent_amount, status,
        ))
    return tuple(checks)
