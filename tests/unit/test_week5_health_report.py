"""Regression lock for the numeric Week 5 health report."""

import json
from pathlib import Path

from tests.bench.run_w5_health_eval import build_report

RESULT = Path(__file__).parents[1] / "bench/result-w5-health.json"


def test_week5_report_is_reproducible_and_has_explicit_confusion_matrix() -> None:
    committed = json.loads(RESULT.read_text(encoding="utf-8"))
    computed = build_report()
    assert committed == computed
    assert computed["dataset"] == {
        "cases": 36,
        "evaluation_delay_seconds": 60,
        "negative_cases": 18,
        "positive_cases": 18,
        "rules": 9,
        "scheduler_interval_seconds": 300,
        "source": "tests/fixtures/monitoring/health-alerts/incidents.yaml",
    }
    assert computed["overall"] == {
        "tp": 18,
        "fp": 0,
        "fn": 0,
        "tn": 18,
        "precision": 1.0,
        "recall": 1.0,
        "mttd_seconds": 460.0,
        "targets_met": True,
    }
    assert computed["targets"] == {"precision_min": 0.70, "recall_min": 0.80}
    assert len(computed["per_rule"]) == 9
