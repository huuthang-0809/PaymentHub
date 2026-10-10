from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

from shared_kernel import ensure_utc

from .health import EvaluationScope, HealthReason, Severity


class AlertAction(str, Enum):
    OPEN = "OPEN"
    UPDATE = "UPDATE"
    RESOLVE = "RESOLVE"


class AlertStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


@dataclass(frozen=True, slots=True)
class AlertRecord:
    alert_id: str
    scope: EvaluationScope
    rule_id: str
    rule_version: int
    status: AlertStatus
    severity: Severity
    reason: HealthReason
    occurrence_count: int
    cooldown_seconds: int
    opened_at: datetime
    last_seen_at: datetime
    last_evaluated_at: datetime
    last_notification_attempt_at: datetime | None = None
    last_notified_at: datetime | None = None
    resolved_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.alert_id.strip() or not self.rule_id.strip():
            raise ValueError("alert_id and rule_id cannot be empty")
        if self.rule_version < 1 or self.occurrence_count < 1:
            raise ValueError("rule_version and occurrence_count must be positive")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds cannot be negative")
        for field in ("opened_at", "last_seen_at", "last_evaluated_at"):
            object.__setattr__(self, field, ensure_utc(getattr(self, field)))
        for field in (
            "last_notification_attempt_at",
            "last_notified_at",
            "resolved_at",
        ):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, ensure_utc(value))
        if self.status is AlertStatus.OPEN and self.resolved_at is not None:
            raise ValueError("OPEN alert cannot have resolved_at")
        if self.status is AlertStatus.RESOLVED and self.resolved_at is None:
            raise ValueError("RESOLVED alert requires resolved_at")

    def mark_notified(self, notified_at: datetime) -> "AlertRecord":
        return replace(self, last_notified_at=ensure_utc(notified_at))


@dataclass(frozen=True, slots=True)
class AlertTransition:
    alert_id: str
    scope: EvaluationScope
    rule_id: str
    rule_version: int
    action: AlertAction
    occurrence_count: int
    notification_due: bool

    def __post_init__(self) -> None:
        if not self.alert_id.strip() or not self.rule_id.strip():
            raise ValueError("alert_id and rule_id cannot be empty")
        if self.rule_version < 1:
            raise ValueError("rule_version must be positive")
        if self.occurrence_count < 1:
            raise ValueError("occurrence_count must be positive")
