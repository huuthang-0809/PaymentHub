from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from canonical.domain import CanonicalTransaction
from shared_kernel import AmountStatus, CanonicalStatus, Region, TxnType, ensure_utc

from .window import SUPPORTED_WINDOW_MINUTES, Window


_OPERATION_TYPES = frozenset({TxnType.PAYMENT, TxnType.LOAD, TxnType.REFUND})
_SUCCESS_STATUSES = frozenset(
    {
        CanonicalStatus.SUCCESS,
        CanonicalStatus.REFUNDED,
        CanonicalStatus.PARTIAL_REFUNDED,
    }
)
_FAILED_STATUSES = frozenset(
    {CanonicalStatus.FAILED, CanonicalStatus.EXPIRED, CanonicalStatus.CANCELLED}
)
_PENDING_STATUSES = frozenset({CanonicalStatus.CREATED, CanonicalStatus.PENDING})
_MONETARY_AMOUNT_STATUSES = frozenset(
    {AmountStatus.OK, AmountStatus.ASSUMED_CURRENCY}
)
_RATE_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class CanonicalEvent:
    event_id: str
    transaction: CanonicalTransaction
    parse_error: bool = False
    is_late: bool = False

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id cannot be empty")

    @property
    def event_time(self) -> datetime:
        return self.transaction.updated_at or self.transaction.created_at


@dataclass(frozen=True, slots=True)
class AggregateKey:
    window: Window
    region: Region
    tenant_scope: str
    provider: str
    txn_type: TxnType
    instrument: str
    channel_raw: str
    currency: str


@dataclass(frozen=True, slots=True)
class WindowAggregate:
    key: AggregateKey
    total_event_count: int
    operation_event_count: int
    success_count: int
    failed_count: int
    pending_count: int
    unknown_count: int
    success_rate: Decimal | None
    failed_rate: Decimal | None
    unknown_rate: Decimal | None
    monetary_count: int
    monetary_amount_minor: int
    invalid_amount_count: int
    missing_currency_count: int
    parse_error_count: int
    late_event_count: int
    non_terminal_count: int
    pending_age_lt_5m_count: int
    pending_age_5_to_15m_count: int
    pending_age_15_to_60m_count: int
    pending_age_gte_60m_count: int
    oldest_pending_age_seconds: int | None
    freshness_lag_seconds: int
    last_event_at: datetime
    computed_at: datetime


@dataclass(slots=True)
class _Accumulator:
    total_event_count: int = 0
    operation_event_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    pending_count: int = 0
    unknown_count: int = 0
    monetary_count: int = 0
    monetary_amount_minor: int = 0
    invalid_amount_count: int = 0
    missing_currency_count: int = 0
    parse_error_count: int = 0
    late_event_count: int = 0
    non_terminal_count: int = 0
    pending_age_lt_5m_count: int = 0
    pending_age_5_to_15m_count: int = 0
    pending_age_15_to_60m_count: int = 0
    pending_age_gte_60m_count: int = 0
    oldest_pending_age_seconds: int | None = None
    last_event_at: datetime | None = None


def _rate(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(
        _RATE_QUANTUM, rounding=ROUND_HALF_UP
    )


def _key(transaction: CanonicalTransaction, event_time: datetime, size: int):
    return AggregateKey(
        window=Window.containing(event_time, size),
        region=transaction.region,
        tenant_scope=transaction.tenant_id or "",
        provider=transaction.provider,
        txn_type=transaction.txn_type,
        instrument=transaction.instrument.value if transaction.instrument else "",
        channel_raw=transaction.channel_raw or "",
        currency=transaction.currency or "",
    )


def _is_operation(transaction: CanonicalTransaction) -> bool:
    return transaction.txn_type in _OPERATION_TYPES and transaction.is_reconcilable


def _is_monetary(transaction: CanonicalTransaction) -> bool:
    return (
        _is_operation(transaction)
        and transaction.is_money_movement
        and transaction.amount_status in _MONETARY_AMOUNT_STATUSES
        and transaction.amount_minor is not None
        and transaction.currency is not None
    )


def _add_event_metrics(accumulator: _Accumulator, event: CanonicalEvent) -> None:
    transaction = event.transaction
    accumulator.total_event_count += 1
    if accumulator.last_event_at is None or event.event_time > accumulator.last_event_at:
        accumulator.last_event_at = event.event_time

    if event.parse_error:
        accumulator.parse_error_count += 1
    if event.is_late:
        accumulator.late_event_count += 1

    if not _is_operation(transaction):
        return

    accumulator.operation_event_count += 1
    if transaction.status in _SUCCESS_STATUSES:
        accumulator.success_count += 1
    elif transaction.status in _FAILED_STATUSES:
        accumulator.failed_count += 1
    elif transaction.status in _PENDING_STATUSES:
        accumulator.pending_count += 1
    else:
        accumulator.unknown_count += 1

    if transaction.amount_status is AmountStatus.INVALID:
        accumulator.invalid_amount_count += 1
    if transaction.amount_status is AmountStatus.NO_CURRENCY:
        accumulator.missing_currency_count += 1
    if _is_monetary(transaction):
        if transaction.amount_minor is None:
            raise AssertionError("monetary eligibility requires amount_minor")
        accumulator.monetary_count += 1
        accumulator.monetary_amount_minor += transaction.amount_minor


def _add_latest_state_metrics(
    accumulator: _Accumulator,
    event: CanonicalEvent,
    computed_at: datetime,
) -> None:
    transaction = event.transaction
    if not _is_operation(transaction) or transaction.is_terminal:
        return
    accumulator.non_terminal_count += 1
    if transaction.status not in _PENDING_STATUSES:
        return

    age_seconds = max(0, int((computed_at - transaction.created_at).total_seconds()))
    if age_seconds < 5 * 60:
        accumulator.pending_age_lt_5m_count += 1
    elif age_seconds < 15 * 60:
        accumulator.pending_age_5_to_15m_count += 1
    elif age_seconds < 60 * 60:
        accumulator.pending_age_15_to_60m_count += 1
    else:
        accumulator.pending_age_gte_60m_count += 1
    if (
        accumulator.oldest_pending_age_seconds is None
        or age_seconds > accumulator.oldest_pending_age_seconds
    ):
        accumulator.oldest_pending_age_seconds = age_seconds


class WindowAggregator:
    def __init__(self, window_sizes: tuple[int, ...] = (5,)) -> None:
        if not window_sizes or len(set(window_sizes)) != len(window_sizes):
            raise ValueError("window sizes must be non-empty and unique")
        unsupported = set(window_sizes) - SUPPORTED_WINDOW_MINUTES
        if unsupported:
            raise ValueError("window sizes must contain only 1, 5 or 15")
        self.window_sizes = window_sizes

    def aggregate(
        self,
        events: Iterable[CanonicalEvent],
        *,
        computed_at: datetime,
    ) -> tuple[WindowAggregate, ...]:
        computed_at = ensure_utc(computed_at)
        unique_events: dict[str, CanonicalEvent] = {}
        for event in events:
            existing = unique_events.get(event.event_id)
            if existing is not None:
                if existing != event:
                    raise ValueError(
                        f"event_id collision with different payload: {event.event_id}"
                    )
                continue
            unique_events[event.event_id] = event

        eligible_events = tuple(
            event
            for event in unique_events.values()
            if not event.transaction.is_deleted and not event.transaction.is_test_data
        )
        accumulators: dict[AggregateKey, _Accumulator] = {}
        for event in eligible_events:
            for size in self.window_sizes:
                key = _key(event.transaction, event.event_time, size)
                _add_event_metrics(accumulators.setdefault(key, _Accumulator()), event)

        latest_by_transaction: dict[object, CanonicalEvent] = {}
        for event in eligible_events:
            identity = event.transaction.global_key
            current = latest_by_transaction.get(identity)
            if current is None or (event.event_time, event.event_id) > (
                current.event_time,
                current.event_id,
            ):
                latest_by_transaction[identity] = event
        for event in latest_by_transaction.values():
            for size in self.window_sizes:
                key = _key(event.transaction, event.event_time, size)
                _add_latest_state_metrics(accumulators[key], event, computed_at)

        results = [
            WindowAggregate(
                key=key,
                total_event_count=value.total_event_count,
                operation_event_count=value.operation_event_count,
                success_count=value.success_count,
                failed_count=value.failed_count,
                pending_count=value.pending_count,
                unknown_count=value.unknown_count,
                success_rate=_rate(value.success_count, value.operation_event_count),
                failed_rate=_rate(value.failed_count, value.operation_event_count),
                unknown_rate=_rate(value.unknown_count, value.operation_event_count),
                monetary_count=value.monetary_count,
                monetary_amount_minor=value.monetary_amount_minor,
                invalid_amount_count=value.invalid_amount_count,
                missing_currency_count=value.missing_currency_count,
                parse_error_count=value.parse_error_count,
                late_event_count=value.late_event_count,
                non_terminal_count=value.non_terminal_count,
                pending_age_lt_5m_count=value.pending_age_lt_5m_count,
                pending_age_5_to_15m_count=value.pending_age_5_to_15m_count,
                pending_age_15_to_60m_count=value.pending_age_15_to_60m_count,
                pending_age_gte_60m_count=value.pending_age_gte_60m_count,
                oldest_pending_age_seconds=value.oldest_pending_age_seconds,
                freshness_lag_seconds=max(
                    0, int((computed_at - value.last_event_at).total_seconds())
                ),
                last_event_at=value.last_event_at,
                computed_at=computed_at,
            )
            for key, value in accumulators.items()
            if value.last_event_at is not None
        ]
        return tuple(sorted(results, key=_sort_key))


def _sort_key(aggregate: WindowAggregate) -> tuple[object, ...]:
    key = aggregate.key
    return (
        key.window.start,
        key.window.size_minutes,
        key.region.value,
        key.tenant_scope,
        key.provider,
        key.txn_type.value,
        key.instrument,
        key.channel_raw,
        key.currency,
    )
