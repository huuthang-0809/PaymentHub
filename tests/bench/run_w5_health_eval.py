"""Compute deterministic Week 5 precision/recall/MTTD acceptance metrics."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from monitoring.adapters.health_config import JsonRuleConfigRepository
from monitoring.domain import (
    EvaluationScope,
    EvaluationStatus,
    HealthMetric,
    HealthWindow,
    MetricRule,
)
from shared_kernel import Region

ROOT = Path(__file__).parents[2]
CONFIG = ROOT / "config/health-rules.week5-provisional.json"
SCOPE = EvaluationScope(Region.VN, "", "BENCH_PROVIDER")
BASE = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
INTERVAL_SECONDS = 300
EVALUATION_DELAY_SECONDS = 60


def _window(config, *, positive: bool, offset: int) -> tuple[HealthWindow, datetime]:
    size = config.window_size_minutes or 15
    end = BASE + timedelta(hours=offset + 1)
    evaluated_at = end + timedelta(seconds=EVALUATION_DELAY_SECONDS)
    sample = max(config.minimum_sample or 100, 100)
    if config.metric is HealthMetric.SUCCESS_RATE:
        rate = config.threshold - Decimal("0.10") if positive else config.threshold + Decimal("0.05")
        rate = min(Decimal(1), max(Decimal(0), rate))
        success = int(rate * sample)
        failed = sample - success
        data_as_of = end - timedelta(seconds=1)
    elif config.metric is HealthMetric.FAILURE_RATE:
        rate = config.threshold + Decimal("0.10") if positive else config.threshold - Decimal("0.05")
        rate = min(Decimal(1), max(Decimal(0), rate))
        failed = int(rate * sample)
        success = sample - failed
        data_as_of = end - timedelta(seconds=1)
    else:
        success, failed = sample, 0
        lag = int(config.threshold) + 1 if positive else max(0, int(config.threshold) - 1)
        data_as_of = evaluated_at - timedelta(seconds=lag)
        end = data_as_of + timedelta(seconds=1)
    start = end - timedelta(minutes=size)
    return HealthWindow(
        scope=SCOPE,
        window_size_minutes=size,
        window_start=start,
        window_end=end,
        operation_event_count=sample,
        success_count=success,
        failed_count=failed,
        data_as_of=data_as_of,
    ), evaluated_at


def build_report() -> dict:
    configs = JsonRuleConfigRepository(
        CONFIG, required_profile="WEEK5_PROVISIONAL"
    ).list_active()
    totals = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    rules = []
    detection_samples = []
    for index, config in enumerate(configs):
        confusion = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        for case in range(4):
            expected_incident = case < 2
            window, evaluated_at = _window(
                config, positive=expected_incident, offset=index * 4 + case
            )
            result = MetricRule(config).evaluate(
                (window,), evaluated_at=evaluated_at, data_as_of=window.data_as_of
            )
            predicted_incident = result.status is EvaluationStatus.BREACH
            key = (
                "tp" if expected_incident and predicted_incident else
                "fn" if expected_incident else
                "fp" if predicted_incident else "tn"
            )
            confusion[key] += 1
            totals[key] += 1
        mttd = (
            config.breach_windows * INTERVAL_SECONDS + EVALUATION_DELAY_SECONDS
        )
        detection_samples.extend([mttd] * confusion["tp"])
        precision = confusion["tp"] / (confusion["tp"] + confusion["fp"])
        recall = confusion["tp"] / (confusion["tp"] + confusion["fn"])
        rules.append({
            "rule_id": config.rule_id,
            **confusion,
            "precision": precision,
            "recall": recall,
            "mttd_seconds": mttd,
        })
    precision = totals["tp"] / (totals["tp"] + totals["fp"])
    recall = totals["tp"] / (totals["tp"] + totals["fn"])
    return {
        "schema_version": 1,
        "profile": "WEEK5_PROVISIONAL",
        "contract_status": "AWAITING_TECHNICAL_LEAD_APPROVAL",
        "dataset": {
            "rules": len(configs),
            "cases_per_rule": 4,
            "positive_cases_per_rule": 2,
            "negative_cases_per_rule": 2,
            "scheduler_interval_seconds": INTERVAL_SECONDS,
            "evaluation_delay_seconds": EVALUATION_DELAY_SECONDS,
        },
        "overall": {
            **totals,
            "precision": precision,
            "recall": recall,
            "mttd_seconds": sum(detection_samples) / len(detection_samples),
        },
        "per_rule": rules,
        "limitations": [
            "Synthetic balanced acceptance fixture; not production prevalence.",
            "MTTD starts at the beginning of the first breached closed window.",
            "Thresholds remain provisional until Technical Lead approval."
        ],
    }


if __name__ == "__main__":
    print(json.dumps(build_report(), indent=2, sort_keys=True))
