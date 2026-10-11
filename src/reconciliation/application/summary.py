"""Financial summary rebuilt from persisted reconciliation results."""

from collections import defaultdict
from dataclasses import dataclass, field
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
class SummaryBreakdown:
    region: str
    tenant_id: str | None
    provider: str
    txn_type: str | None
    result: str
    system_count: int
    provider_count: int
    totals_by_currency: tuple[CurrencyTotals, ...]
    unallocated_currency_count: int


@dataclass(frozen=True)
class RefundCurrencyTotals:
    currency: str | None
    count: int
    amount_minor: int | None
    missing_amount_count: int


@dataclass(frozen=True)
class FinancialSummary:
    totals_by_currency: tuple[CurrencyTotals, ...]
    unallocated_currency_count: int
    refund_parent_metrics: RefundParentMetrics
    refund_cross_checks: tuple[RefundCrossCheck, ...]
    breakdowns: tuple[SummaryBreakdown, ...]
    refunds_by_currency: tuple[RefundCurrencyTotals, ...]


class _CurrencyAccumulator:
    def __init__(self) -> None:
        self.amounts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.unallocated = 0

    def add(self, row: ReconResult) -> None:
        for side, present in (
            ("system", row.system_txn_id is not None),
            ("provider", row.source_row_number is not None),
        ):
            amount = getattr(row, f"{side}_amount_minor")
            currency = getattr(row, f"{side}_currency")
            if not present and amount is None and currency is None:
                continue
            if not currency:
                self.unallocated += 1
                continue
            bucket = self.amounts[currency]
            if amount is None or (
                side == "system" and row.result_type is ReconResultType.INVALID_DATA
            ):
                bucket[f"{side}_missing"] += 1
            else:
                bucket[side] += int(amount)
                bucket[f"{side}_count"] += 1

    def finish(self) -> tuple[tuple[CurrencyTotals, ...], int]:
        totals = tuple(
            CurrencyTotals(
                currency=currency,
                system_amount_minor=bucket["system"] if bucket["system_count"] else None,
                provider_amount_minor=bucket["provider"] if bucket["provider_count"] else None,
                delta_amount_minor=(
                    bucket["system"] - bucket["provider"]
                    if not (bucket["system_missing"] or bucket["provider_missing"] or self.unallocated)
                    else None
                ),
                system_amount_count=bucket["system_count"],
                provider_amount_count=bucket["provider_count"],
                system_missing_amount_count=bucket["system_missing"],
                provider_missing_amount_count=bucket["provider_missing"],
            )
            for currency, bucket in sorted(self.amounts.items())
        )
        return totals, self.unallocated


@dataclass(slots=True)
class _BreakdownAccumulator:
    system_count: int = 0
    provider_count: int = 0
    currencies: _CurrencyAccumulator = field(default_factory=_CurrencyAccumulator)


@dataclass(slots=True)
class _RefundAccumulator:
    count: int = 0
    valid_count: int = 0
    amount: int = 0

    def add(self, row: ReconResult) -> None:
        self.count += 1
        if row.system_amount_minor is not None and row.result_type is not ReconResultType.INVALID_DATA:
            self.valid_count += 1
            self.amount += int(row.system_amount_minor)


@dataclass(slots=True)
class _RefundChildren:
    count: int = 0
    successful_count: int = 0
    amount: int = 0
    incomplete: bool = False

    def add(self, row: ReconResult) -> None:
        self.count += 1
        if row.system_status in {None, CanonicalStatus.UNKNOWN}:
            self.incomplete = True
        if row.system_status in SUCCESSFUL_REFUND_STATUSES:
            self.successful_count += 1
            if row.system_amount_minor is None or row.result_type is ReconResultType.INVALID_DATA:
                self.incomplete = True
            else:
                self.amount += int(row.system_amount_minor)


def _is_refunded(parent: ReconResult) -> bool:
    return parent.system_status in REFUNDED_STATUSES or (
        parent.refunded_amount_minor is not None and parent.refunded_amount_minor > 0
    )


def _parent_key(parent: ReconResult) -> tuple:
    reference = (
        parent.system_txn_id if parent.region is Region.VN
        else parent.system_provider_txn_id
    )
    return parent.region, parent.tenant_id, parent.provider, reference


def build_financial_summary(results: Iterable[ReconResult]) -> FinancialSummary:
    """Consume results once; retain counters and only refund lookup evidence."""
    currencies = _CurrencyAccumulator()
    breakdowns: dict[tuple, _BreakdownAccumulator] = defaultdict(_BreakdownAccumulator)
    refunds: dict[str | None, _RefundAccumulator] = defaultdict(_RefundAccumulator)
    children: dict[tuple, _RefundChildren] = defaultdict(_RefundChildren)
    # Parent references are needed for out-of-order refund children and ambiguity.
    # No full-run tuple or per-breakdown/per-child lists are materialized.
    parents: list[ReconResult] = []
    parent_count = refunded = missing = 0
    for row in results:
        key = (row.region.value, row.tenant_id, row.provider,
               row.txn_type.value if row.txn_type else None, row.result_type.value)
        group = breakdowns[key]
        group.system_count += row.system_txn_id is not None
        group.provider_count += row.source_row_number is not None
        if row.result_type in {ReconResultType.DUPLICATE, ReconResultType.NOT_RECONCILABLE}:
            continue
        currencies.add(row)
        group.currencies.add(row)
        if row.system_txn_id and row.txn_type in {TxnType.PAYMENT, TxnType.LOAD}:
            parent_count += 1
            is_refunded = _is_refunded(row)
            refunded += is_refunded
            missing += row.system_status in {None, CanonicalStatus.UNKNOWN} and not is_refunded
            if row.region in {Region.VN, Region.INDIA}:
                parents.append(row)
        if row.system_txn_id is not None and row.txn_type is TxnType.REFUND:
            if row.system_status in SUCCESSFUL_REFUND_STATUSES:
                refunds[row.system_currency].add(row)
            if row.system_txn_id and row.region in {Region.VN, Region.INDIA}:
                children[(row.region, row.tenant_id, row.provider,
                          row.refund_of, row.system_currency)].add(row)

    totals, unallocated = currencies.finish()
    rate = (
        format(Decimal(refunded) / Decimal(parent_count), ".6f")
        if parent_count and not missing else None
    )
    output_breakdowns = []
    for key, group in sorted(breakdowns.items(), key=lambda item: str(item[0])):
        group_totals, group_unallocated = group.currencies.finish()
        output_breakdowns.append(SummaryBreakdown(
            *key, group.system_count, group.provider_count, group_totals, group_unallocated,
        ))
    refund_totals = tuple(
        RefundCurrencyTotals(
            currency, group.count,
            group.amount if group.valid_count and currency else None,
            group.count - group.valid_count,
        )
        for currency, group in sorted(refunds.items(), key=lambda item: str(item[0]))
    )
    return FinancialSummary(
        totals, unallocated,
        RefundParentMetrics(parent_count, refunded, missing, rate),
        _refund_cross_checks(children, parents), tuple(output_breakdowns), refund_totals,
    )


def _refund_cross_checks(
    groups: dict[tuple, _RefundChildren], parents: list[ReconResult],
) -> tuple[RefundCrossCheck, ...]:
    # First establish all references requiring a check, including parents without
    # children. Only index parents for those references, preserving ambiguity.
    for parent in parents:
        key = _parent_key(parent)
        if key[3] and _is_refunded(parent):
            groups[(*key, parent.system_currency)]
    required = {key[:4] for key in groups if key[3]}
    parent_index: dict[tuple, tuple[int, ReconResult]] = {}
    for parent in parents:
        key = _parent_key(parent)
        if key in required:
            count, _ = parent_index.get(key, (0, parent))
            parent_index[key] = count + 1, parent

    checks = []
    for key, children in sorted(groups.items(), key=lambda item: str(item[0])):
        region, tenant, provider, reference, currency = key
        candidate_count, candidate = parent_index.get(key[:4], (0, None))
        parent = candidate if candidate_count == 1 else None
        child_amount = (
            children.amount if children.count and not children.incomplete and currency else None
        )
        parent_amount = (
            int(parent.refunded_amount_minor)
            if parent and parent.refunded_amount_minor is not None else None
        )
        if not reference:
            status = "MISSING_PARENT_REFERENCE"
        elif candidate_count > 1:
            status = "AMBIGUOUS_PARENT"
        elif parent is None:
            status = "PARENT_NOT_IN_SNAPSHOT"
        elif currency is None or parent.system_currency is None:
            status = "MISSING_CURRENCY"
        elif parent.system_currency != currency:
            status = "CURRENCY_MISMATCH"
        elif not children.count:
            status = "CHILDREN_NOT_IN_SNAPSHOT"
        elif children.incomplete:
            status = "INCOMPLETE_CHILD_DATA"
        elif region is Region.VN:
            status = "LINKED"
        elif parent_amount is None:
            status = "PARENT_TOTAL_UNAVAILABLE"
        else:
            status = "MATCHED" if child_amount == parent_amount else "AMOUNT_MISMATCH"
        checks.append(RefundCrossCheck(
            region.value, tenant, provider, reference, currency,
            children.successful_count, child_amount, parent_amount, status,
        ))
    return tuple(checks)
