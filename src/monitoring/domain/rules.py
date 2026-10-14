from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from .health import (
    Comparison,
    EvaluationStatus,
    HealthMetric,
    HealthReason,
    HealthWindow,
    RuleConfig,
    RuleEvaluation,
)


_RATE_QUANTUM = Decimal("0.000001")


class MetricRule:
    def __init__(self, config: RuleConfig) -> None:
        self._config = config

    @property
    def config(self) -> RuleConfig:
        return self._config

    def evaluate(
        self,
        windows: tuple[HealthWindow, ...],
        *,
        evaluated_at: datetime,
        data_as_of: datetime | None,
    ) -> RuleEvaluation:
        if self.config.metric is HealthMetric.FRESHNESS_LAG_SECONDS:
            return self._freshness(evaluated_at, data_as_of)
        if self.config.metric in {HealthMetric.SUCCESS_RATE, HealthMetric.FAILURE_RATE}:
            return self._rate(windows, data_as_of)
        return self._operational(windows, data_as_of)

    def _operational(
        self,
        windows: tuple[HealthWindow, ...],
        data_as_of: datetime | None,
    ) -> RuleEvaluation:
        selected = tuple(
            sorted(
                (
                    window for window in windows
                    if window.window_size_minutes == self.config.window_size_minutes
                ),
                key=lambda window: window.window_end,
                reverse=True,
            )[: self.config.lookback_windows]
        )
        if not selected:
            return self._result(
                EvaluationStatus.INSUFFICIENT_DATA,
                reason_code="NO_CLOSED_WINDOW",
                message="no closed window is available",
                observed_value=None,
                sample_size=None,
                windows=(),
                data_as_of=data_as_of,
            )
        metric = self.config.metric
        if metric is HealthMetric.VOLUME_RATIO:
            current = sum(item.total_event_count for item in selected)
            baseline = sum(item.baseline_event_count for item in selected)
            if baseline <= 0:
                return self._result(
                    EvaluationStatus.INSUFFICIENT_DATA,
                    reason_code="NO_VOLUME_BASELINE",
                    message="same-slot volume baseline is unavailable",
                    observed_value=None,
                    sample_size=current,
                    windows=selected,
                    data_as_of=data_as_of,
                )
            observed = (Decimal(current) / Decimal(baseline)).quantize(_RATE_QUANTUM)
        else:
            field = {
                HealthMetric.DATA_QUALITY_ERROR_COUNT: "data_quality_error_count",
                HealthMetric.QUEUE_PRESSURE: "queue_pressure",
                HealthMetric.ASIA_QR_VA_PENDING_OVER_24H: "asia_qr_va_pending_age_seconds",
                HealthMetric.ASIA_REFUND_SHAPE_B_PENDING: "asia_refund_shape_b_pending_age_seconds",
                HealthMetric.INDIA_NON_TERMINAL_OVER_AGE: "india_non_terminal_age_seconds",
                HealthMetric.INDIA_REFUND_CREATED_OVER_AGE: "india_refund_created_age_seconds",
            }[metric]
            values = []
            for item in selected:
                if field == "data_quality_error_count":
                    values.append(item.unknown_count + item.invalid_amount_count)
                elif field == "queue_pressure":
                    values.append(max(item.queue_lag_seconds, item.dlq_count))
                else:
                    values.append(getattr(item, field))
            observed = Decimal(max(values, default=0))
        breached = _compare(observed, self.config.comparison, self.config.threshold)
        return self._result(
            EvaluationStatus.BREACH if breached else EvaluationStatus.PASS,
            reason_code=f"{metric.value.upper()}_{'BREACH' if breached else 'PASS'}",
            message=f"{metric.value} {'breached' if breached else 'passed'} configured threshold",
            observed_value=observed,
            sample_size=sum(item.total_event_count for item in selected),
            windows=selected,
            data_as_of=data_as_of,
        )

    def _rate(
        self,
        windows: tuple[HealthWindow, ...],
        data_as_of: datetime | None,
    ) -> RuleEvaluation:
        selected = tuple(
            sorted(
                (
                    window
                    for window in windows
                    if window.window_size_minutes == self.config.window_size_minutes
                ),
                key=lambda window: window.window_end,
                reverse=True,
            )[: self.config.lookback_windows]
        )
        sample_size = sum(window.operation_event_count for window in selected)
        if sample_size < (self.config.minimum_sample or 0):
            return self._result(
                EvaluationStatus.INSUFFICIENT_DATA,
                reason_code="INSUFFICIENT_SAMPLE",
                message="operation sample is below the configured minimum",
                observed_value=None,
                sample_size=sample_size,
                windows=selected,
                data_as_of=data_as_of,
            )

        numerator = sum(
            window.success_count
            if self.config.metric is HealthMetric.SUCCESS_RATE
            else window.failed_count
            for window in selected
        )
        observed = (Decimal(numerator) / Decimal(sample_size)).quantize(
            _RATE_QUANTUM, rounding=ROUND_HALF_UP
        )
        breached = _compare(observed, self.config.comparison, self.config.threshold)
        return self._result(
            EvaluationStatus.BREACH if breached else EvaluationStatus.PASS,
            reason_code="THRESHOLD_BREACH" if breached else "THRESHOLD_PASS",
            message="rate breached configured threshold" if breached else "rate passed",
            observed_value=observed,
            sample_size=sample_size,
            windows=selected,
            data_as_of=data_as_of,
        )

    def _freshness(
        self,
        evaluated_at: datetime,
        data_as_of: datetime | None,
    ) -> RuleEvaluation:
        if data_as_of is None:
            return self._result(
                EvaluationStatus.INSUFFICIENT_DATA,
                reason_code="NO_DATA_AS_OF",
                message="freshness cannot be evaluated without data_as_of",
                observed_value=None,
                sample_size=None,
                windows=(),
                data_as_of=None,
            )
        lag = Decimal(max(0, int((evaluated_at - data_as_of).total_seconds())))
        breached = _compare(lag, self.config.comparison, self.config.threshold)
        return self._result(
            EvaluationStatus.BREACH if breached else EvaluationStatus.PASS,
            reason_code="STALE_DATA" if breached else "FRESH_DATA",
            message="data is stale" if breached else "data is fresh",
            observed_value=lag,
            sample_size=None,
            windows=(),
            data_as_of=data_as_of,
        )

    def _result(
        self,
        status: EvaluationStatus,
        *,
        reason_code: str,
        message: str,
        observed_value: Decimal | None,
        sample_size: int | None,
        windows: tuple[HealthWindow, ...],
        data_as_of: datetime | None,
    ) -> RuleEvaluation:
        starts = tuple(window.window_start for window in windows)
        ends = tuple(window.window_end for window in windows)
        reason = HealthReason(
            rule_id=self.config.rule_id,
            rule_version=self.config.rule_version,
            evaluation_status=status,
            health_impact=self.config.health_impact,
            reason_code=reason_code,
            message=message,
            observed_value=observed_value,
            comparison=self.config.comparison,
            threshold=self.config.threshold,
            sample_size=sample_size,
            minimum_sample=self.config.minimum_sample,
            window_size_minutes=self.config.window_size_minutes,
            evaluated_window_count=len(windows),
            first_window_start=min(starts, default=None),
            last_window_end=max(ends, default=None),
            runbook_ref=self.config.runbook_ref,
            data_as_of=data_as_of,
            txn_ids=tuple(
                dict.fromkeys(
                    txn_id
                    for window in windows
                    for txn_id in window.evidence_txn_ids.get(self.config.metric, ())
                )
            )[:5],
        )
        return RuleEvaluation(config=self.config, status=status, reason=reason)


def _compare(observed: Decimal, comparison: Comparison, threshold: Decimal) -> bool:
    if comparison is Comparison.LT:
        return observed < threshold
    if comparison is Comparison.GTE:
        return observed >= threshold
    if comparison is Comparison.GT:
        return observed > threshold
    raise ValueError(f"unsupported comparison: {comparison}")
