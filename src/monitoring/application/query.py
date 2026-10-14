from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from shared_kernel import Region, ensure_utc

from .ports import WindowMetricQueryRepository


class MonitoringUnavailable(RuntimeError):
    """Raised when a monitoring query cannot reach PostgreSQL in time."""


@dataclass(frozen=True, slots=True)
class WindowMetricQuery:
    region: Region | None = None
    tenant_id: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    window_size: int = 5
    limit: int = 100
    offset: int = 0
    sort: str = "-window_start"

    def __post_init__(self) -> None:
        if self.window_size not in {1, 5, 15}:
            raise ValueError("window_size must be one of 1, 5 or 15")
        if not 1 <= self.limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if self.offset < 0:
            raise ValueError("cursor offset cannot be negative")
        if self.sort != "-window_start":
            raise ValueError("sort must be one of: -window_start")
        if self.tenant_id is not None:
            tenant = self.tenant_id.strip()
            if not tenant or len(tenant) > 100:
                raise ValueError("tenant_id must contain 1 to 100 characters")
            object.__setattr__(self, "tenant_id", tenant)
        if self.start is not None:
            object.__setattr__(self, "start", ensure_utc(self.start))
        if self.end is not None:
            object.__setattr__(self, "end", ensure_utc(self.end))
        if self.start is not None and self.end is not None:
            if self.end <= self.start:
                raise ValueError("end must be later than start")
            if self.end - self.start > timedelta(days=31):
                raise ValueError("time range cannot exceed 31 days")


@dataclass(frozen=True, slots=True)
class WindowMetric:
    window_size_minutes: int
    window_start: datetime
    window_end: datetime
    region: Region
    tenant_id: str | None
    provider: str
    txn_type: str
    instrument: str | None
    channel_raw: str | None
    currency: str | None
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
    source_event_count: int
    last_event_at: datetime
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class WindowMetricPage:
    items: Sequence[WindowMetric]
    data_as_of: datetime | None
    next_offset: int | None = None


class MonitoringQueryService:
    def __init__(self, repository: WindowMetricQueryRepository) -> None:
        self.repository = repository

    def get_windows(self, query: WindowMetricQuery) -> WindowMetricPage:
        return self.repository.list_window_metrics(query)
