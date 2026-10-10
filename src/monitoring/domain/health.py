from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Protocol

from shared_kernel import Region, ensure_utc


class EvaluationStatus(str, Enum):
    PASS = "PASS"
    BREACH = "BREACH"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    CONFIG_ERROR = "CONFIG_ERROR"


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    NO_DATA = "NO_DATA"


class HealthImpact(str, Enum):
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


class Severity(str, Enum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class Comparison(str, Enum):
    LT = "LT"
    GTE = "GTE"
    GT = "GT"


class HealthMetric(str, Enum):
    SUCCESS_RATE = "success_rate"
    FAILURE_RATE = "failure_rate"
    FRESHNESS_LAG_SECONDS = "freshness_lag_seconds"


@dataclass(frozen=True, slots=True)
class EvaluationScope:
    region: Region
    tenant_scope: str
    provider: str

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider cannot be empty")
        if self.region is Region.ASIA and not self.tenant_scope.strip():
            raise ValueError("ASIA health scope requires tenant_scope")


@dataclass(frozen=True, slots=True)
class HealthWindow:
    scope: EvaluationScope
    window_size_minutes: int
    window_start: datetime
    window_end: datetime
    operation_event_count: int
    success_count: int
    failed_count: int
    data_as_of: datetime

    def __post_init__(self) -> None:
        start = ensure_utc(self.window_start)
        end = ensure_utc(self.window_end)
        data_as_of = ensure_utc(self.data_as_of)
        if self.window_size_minutes not in {1, 5, 15}:
            raise ValueError("window size must be one of 1, 5 or 15 minutes")
        if (end - start).total_seconds() != self.window_size_minutes * 60:
            raise ValueError("window bounds do not match window size")
        if not start <= data_as_of < end:
            raise ValueError("data_as_of must be inside the source window")
        counts = (
            self.operation_event_count,
            self.success_count,
            self.failed_count,
        )
        if any(value < 0 for value in counts):
            raise ValueError("health window counts cannot be negative")
        if self.success_count + self.failed_count > self.operation_event_count:
            raise ValueError("status counts cannot exceed operation count")
        object.__setattr__(self, "window_start", start)
        object.__setattr__(self, "window_end", end)
        object.__setattr__(self, "data_as_of", data_as_of)


@dataclass(frozen=True, slots=True)
class RuleConfig:
    rule_id: str
    rule_version: int
    metric: HealthMetric
    comparison: Comparison
    threshold: Decimal
    minimum_sample: int | None
    window_size_minutes: int | None
    lookback_windows: int
    breach_windows: int
    recovery_windows: int
    health_impact: HealthImpact
    severity: Severity
    cooldown_seconds: int
    runbook_ref: str

    def __post_init__(self) -> None:
        if not self.rule_id.strip():
            raise ValueError("rule_id cannot be empty")
        if self.rule_version < 1:
            raise ValueError("rule_version must be positive")
        if self.lookback_windows < 1:
            raise ValueError("lookback_windows must be positive")
        if self.breach_windows < 1 or self.recovery_windows < 1:
            raise ValueError("breach/recovery windows must be positive")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds cannot be negative")
        if not self.runbook_ref.strip():
            raise ValueError("runbook_ref cannot be empty")

        if self.metric in {HealthMetric.SUCCESS_RATE, HealthMetric.FAILURE_RATE}:
            if self.minimum_sample is None or self.minimum_sample < 1:
                raise ValueError("rate rules require a positive minimum_sample")
            if self.window_size_minutes not in {1, 5, 15}:
                raise ValueError("rate rules require a supported window size")
            if not Decimal(0) <= self.threshold <= Decimal(1):
                raise ValueError("rate threshold must be between 0 and 1")
        elif self.metric is HealthMetric.FRESHNESS_LAG_SECONDS:
            if self.minimum_sample is not None or self.window_size_minutes is not None:
                raise ValueError("freshness rules do not use sample/window size")
            if self.threshold < 0:
                raise ValueError("freshness threshold cannot be negative")


@dataclass(frozen=True, slots=True)
class RuleState:
    rule_id: str
    rule_version: int
    consecutive_breaches: int = 0
    consecutive_recoveries: int = 0

    def __post_init__(self) -> None:
        if self.rule_version < 1:
            raise ValueError("rule_version must be positive")
        if self.consecutive_breaches < 0 or self.consecutive_recoveries < 0:
            raise ValueError("consecutive counters cannot be negative")
        if self.consecutive_breaches and self.consecutive_recoveries:
            raise ValueError("breach and recovery streaks cannot both be positive")


@dataclass(frozen=True, slots=True)
class HealthReason:
    rule_id: str
    rule_version: int
    evaluation_status: EvaluationStatus
    health_impact: HealthImpact
    reason_code: str
    message: str
    observed_value: Decimal | None
    comparison: Comparison
    threshold: Decimal
    sample_size: int | None
    minimum_sample: int | None
    window_size_minutes: int | None
    evaluated_window_count: int
    first_window_start: datetime | None
    last_window_end: datetime | None
    runbook_ref: str
    data_as_of: datetime | None


@dataclass(frozen=True, slots=True)
class RuleEvaluation:
    config: RuleConfig
    status: EvaluationStatus
    reason: HealthReason
    consecutive_breaches: int = 0
    consecutive_recoveries: int = 0
    breach_confirmed: bool = False
    recovery_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class HealthEvaluation:
    scope: EvaluationScope
    status: HealthStatus
    evaluations: tuple[RuleEvaluation, ...]
    reasons: tuple[HealthReason, ...]
    next_states: tuple[RuleState, ...]
    evaluated_at: datetime
    data_as_of: datetime | None


class AlertRule(Protocol):
    @property
    def config(self) -> RuleConfig: ...

    def evaluate(
        self,
        windows: tuple[HealthWindow, ...],
        *,
        evaluated_at: datetime,
        data_as_of: datetime | None,
    ) -> RuleEvaluation: ...


def _system_clock() -> datetime:
    return datetime.now(UTC)


class HealthEvaluator:
    def __init__(
        self,
        rules: Iterable[AlertRule],
        *,
        clock: Callable[[], datetime] = _system_clock,
    ) -> None:
        self._rules = tuple(rules)
        if not self._rules:
            raise ValueError("at least one health rule is required")
        identities = {
            (rule.config.rule_id, rule.config.rule_version) for rule in self._rules
        }
        if len(identities) != len(self._rules):
            raise ValueError("health rule identities must be unique")
        self._clock = clock

    def evaluate(
        self,
        scope: EvaluationScope,
        windows: Iterable[HealthWindow],
        *,
        prior_states: Iterable[RuleState] = (),
        data_as_of: datetime | None = None,
        evaluated_at: datetime | None = None,
    ) -> HealthEvaluation:
        evaluated_at = ensure_utc(evaluated_at or self._clock())
        source_windows = tuple(windows)
        if any(window.scope != scope for window in source_windows):
            raise ValueError("all health windows must match evaluation scope")
        if any(window.window_end > evaluated_at for window in source_windows):
            raise ValueError("health evaluation cannot include an open window")

        inferred_data_as_of = max(
            (window.data_as_of for window in source_windows), default=None
        )
        if data_as_of is not None:
            data_as_of = ensure_utc(data_as_of)
            if data_as_of > evaluated_at:
                raise ValueError("data_as_of cannot be after evaluated_at")
        else:
            data_as_of = inferred_data_as_of

        prior_states = tuple(prior_states)
        state_by_rule = {
            (state.rule_id, state.rule_version): state for state in prior_states
        }
        if len(state_by_rule) != len(prior_states):
            raise ValueError("prior rule states must be unique")

        evaluations: list[RuleEvaluation] = []
        next_states: list[RuleState] = []
        for rule in self._rules:
            raw = rule.evaluate(
                source_windows,
                evaluated_at=evaluated_at,
                data_as_of=data_as_of,
            )
            identity = (rule.config.rule_id, rule.config.rule_version)
            previous = state_by_rule.get(identity, RuleState(*identity))
            state = self._advance(previous, raw.status)
            evaluation = replace(
                raw,
                consecutive_breaches=state.consecutive_breaches,
                consecutive_recoveries=state.consecutive_recoveries,
                breach_confirmed=(
                    raw.status is EvaluationStatus.BREACH
                    and state.consecutive_breaches >= rule.config.breach_windows
                ),
                recovery_confirmed=(
                    raw.status is EvaluationStatus.PASS
                    and state.consecutive_recoveries >= rule.config.recovery_windows
                ),
            )
            evaluations.append(evaluation)
            next_states.append(state)

        status = self._health_status(evaluations)
        reasons = tuple(
            evaluation.reason
            for evaluation in evaluations
            if evaluation.status is not EvaluationStatus.PASS
        )
        return HealthEvaluation(
            scope=scope,
            status=status,
            evaluations=tuple(evaluations),
            reasons=reasons,
            next_states=tuple(next_states),
            evaluated_at=evaluated_at,
            data_as_of=data_as_of,
        )

    @staticmethod
    def _advance(state: RuleState, status: EvaluationStatus) -> RuleState:
        if status is EvaluationStatus.BREACH:
            return replace(
                state,
                consecutive_breaches=state.consecutive_breaches + 1,
                consecutive_recoveries=0,
            )
        if status is EvaluationStatus.PASS:
            return replace(
                state,
                consecutive_breaches=0,
                consecutive_recoveries=state.consecutive_recoveries + 1,
            )
        return replace(state, consecutive_breaches=0, consecutive_recoveries=0)

    @staticmethod
    def _health_status(evaluations: Iterable[RuleEvaluation]) -> HealthStatus:
        values = tuple(evaluations)
        breaches = tuple(
            value for value in values if value.status is EvaluationStatus.BREACH
        )
        if any(
            value.config.health_impact is HealthImpact.UNHEALTHY
            for value in breaches
        ):
            return HealthStatus.UNHEALTHY
        if breaches or any(
            value.status is EvaluationStatus.CONFIG_ERROR for value in values
        ):
            return HealthStatus.DEGRADED
        if any(value.status is EvaluationStatus.PASS for value in values):
            return HealthStatus.HEALTHY
        return HealthStatus.NO_DATA
