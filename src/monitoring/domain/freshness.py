from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from shared_kernel import ensure_utc

from .health import EvaluationScope


class FreshnessStatus(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    NO_DATA = "NO_DATA"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class HealthRunnerStatus:
    scope: EvaluationScope
    last_run_at: datetime
    last_success_at: datetime | None
    data_as_of: datetime | None
    freshness_lag_seconds: int | None
    freshness_sla_seconds: int
    freshness_status: FreshnessStatus
    consecutive_failures: int
    last_error_code: str | None = None
    last_error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "last_run_at", ensure_utc(self.last_run_at))
        for field in ("last_success_at", "data_as_of"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, ensure_utc(value))
        if self.freshness_sla_seconds < 0 or self.consecutive_failures < 0:
            raise ValueError("freshness SLA/failure count cannot be negative")
        if self.freshness_lag_seconds is not None and self.freshness_lag_seconds < 0:
            raise ValueError("freshness lag cannot be negative")
