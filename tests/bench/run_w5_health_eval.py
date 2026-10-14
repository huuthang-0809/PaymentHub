"""Replay labeled Week 5 incidents and compute precision, recall and MTTD."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from monitoring.adapters.health_config import JsonRuleConfigRepository
from monitoring.domain import (
    EvaluationScope, HealthEvaluator, HealthMetric, HealthWindow, MetricRule,
)
from shared_kernel import Region

ROOT = Path(__file__).parents[2]
CONFIG = ROOT / "config/health-rules.week5-provisional.yaml"
INCIDENTS = ROOT / "tests/fixtures/monitoring/health-alerts/incidents.yaml"
SCOPE = EvaluationScope(Region.VN, "", "BENCH_PROVIDER")


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _window(config, observed: Decimal, end: datetime, delay: int) -> HealthWindow:
    size = config.window_size_minutes or 15
    evaluated_at = end + timedelta(seconds=delay)
    sample = max(config.minimum_sample or 100, 100)
    success, failed = sample, 0
    data_as_of = end - timedelta(seconds=1)
    values: dict[str, object] = {"total_event_count": sample}
    if config.metric is HealthMetric.FAILURE_RATE:
        failed = int(observed * sample)
        success = sample - failed
    elif config.metric is HealthMetric.FRESHNESS_LAG_SECONDS:
        data_as_of = evaluated_at - timedelta(seconds=int(observed))
    elif config.metric is HealthMetric.VOLUME_RATIO:
        values.update(total_event_count=int(observed * 100), baseline_event_count=100)
    elif config.metric is HealthMetric.DATA_QUALITY_ERROR_COUNT:
        values["unknown_count"] = int(observed)
    elif config.metric is HealthMetric.QUEUE_PRESSURE:
        values["queue_lag_seconds"] = int(observed)
    else:
        fields = {
            HealthMetric.ASIA_QR_VA_PENDING_OVER_24H: "asia_qr_va_pending_age_seconds",
            HealthMetric.ASIA_REFUND_SHAPE_B_PENDING: "asia_refund_shape_b_pending_age_seconds",
            HealthMetric.INDIA_NON_TERMINAL_OVER_AGE: "india_non_terminal_age_seconds",
            HealthMetric.INDIA_REFUND_CREATED_OVER_AGE: "india_refund_created_age_seconds",
        }
        values[fields[config.metric]] = int(observed)
    return HealthWindow(
        scope=SCOPE, window_size_minutes=size,
        window_start=end - timedelta(minutes=size), window_end=end,
        operation_event_count=sample, success_count=success, failed_count=failed,
        data_as_of=data_as_of, **values,
    )


def _replay(config, case, observations, interval: int, delay: int):
    start = _timestamp(case["start"])
    evaluator = HealthEvaluator((MetricRule(config),))
    states = ()
    detected_at = None
    for index, value in enumerate(observations, start=1):
        end = start + timedelta(seconds=index * interval)
        evaluated_at = end + timedelta(seconds=delay)
        result = evaluator.evaluate(
            SCOPE, (_window(config, Decimal(str(value)), end, delay),),
            prior_states=states, evaluated_at=evaluated_at,
        )
        states = result.next_states
        if result.evaluations[0].breach_confirmed and detected_at is None:
            detected_at = evaluated_at
    return detected_at, start


def build_report() -> dict:
    document = json.loads(INCIDENTS.read_text(encoding="utf-8"))
    configs = {
        item.rule_id: item for item in JsonRuleConfigRepository(
            CONFIG, required_profile="WEEK5_PROVISIONAL"
        ).list_active()
    }
    interval = int(document["scheduler_interval_seconds"])
    delay = int(document["evaluation_delay_seconds"])
    totals = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    detection_samples: list[int] = []
    per_rule = []
    for fixture in document["rules"]:
        config = configs[fixture["rule_id"]]
        confusion = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        rule_mttd = []
        for case in fixture["cases"]:
            expected = case["label"] == "INCIDENT"
            observations = fixture[
                "positive_observations" if expected else "negative_observations"
            ]
            detected_at, started_at = _replay(config, case, observations, interval, delay)
            predicted = detected_at is not None
            key = "tp" if expected and predicted else "fn" if expected else "fp" if predicted else "tn"
            confusion[key] += 1
            totals[key] += 1
            if expected and predicted:
                mttd = int((detected_at - started_at).total_seconds())
                rule_mttd.append(mttd)
                detection_samples.append(mttd)
        per_rule.append({
            "rule_id": config.rule_id, **confusion,
            "precision": confusion["tp"] / (confusion["tp"] + confusion["fp"]),
            "recall": confusion["tp"] / (confusion["tp"] + confusion["fn"]),
            "mttd_seconds": sum(rule_mttd) / len(rule_mttd),
        })
    precision = totals["tp"] / (totals["tp"] + totals["fp"])
    recall = totals["tp"] / (totals["tp"] + totals["fn"])
    return {
        "schema_version": 2, "profile": "WEEK5_PROVISIONAL",
        "contract_status": "AWAITING_TECHNICAL_LEAD_APPROVAL",
        "dataset": {
            "source": "tests/fixtures/monitoring/health-alerts/incidents.yaml",
            "rules": len(document["rules"]), "cases": sum(len(x["cases"]) for x in document["rules"]),
            "positive_cases": totals["tp"] + totals["fn"], "negative_cases": totals["tn"] + totals["fp"],
            "scheduler_interval_seconds": interval, "evaluation_delay_seconds": delay,
        },
        "targets": {"precision_min": 0.70, "recall_min": 0.80},
        "overall": {**totals, "precision": precision, "recall": recall,
                    "mttd_seconds": sum(detection_samples) / len(detection_samples),
                    "targets_met": precision >= 0.70 and recall >= 0.80},
        "per_rule": per_rule,
        "limitations": [
            "Labeled synthetic incident timelines; not production prevalence.",
            "Thresholds remain provisional until Technical Lead approval.",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(build_report(), indent=2, sort_keys=True))
