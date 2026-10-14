from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from monitoring.domain import (
    Comparison,
    HealthDecisionPolicy,
    HealthImpact,
    HealthMetric,
    RuleConfig,
    Severity,
)


class FileRuleConfigRepository:
    """Load versioned rule configuration from JSON or JSON-compatible YAML.

    JSON is a valid YAML 1.2 subset, which keeps this adapter dependency-free
    while allowing the deployed contract to use the required `.yaml` file.
    """
    def __init__(self, path: Path | str, *, required_profile: str = "DEMO_ONLY") -> None:
        self.path = Path(path)
        self.required_profile = required_profile

    def list_active(self) -> tuple[RuleConfig, ...]:
        document = self._document()
        if document.get("profile") != self.required_profile:
            raise ValueError(
                f"rule profile must be {self.required_profile!r}"
            )
        rules = tuple(self._rule(item) for item in document.get("rules", ()))
        if not rules:
            raise ValueError("rule config must contain at least one rule")
        identities = {(item.rule_id, item.rule_version) for item in rules}
        if len(identities) != len(rules):
            raise ValueError("rule config identities must be unique")
        return rules

    def decision_policy(self) -> HealthDecisionPolicy:
        values = self._document().get("decision_policy", {})
        return HealthDecisionPolicy(
            unhealthy_requires_distinct_metrics=int(
                values.get("unhealthy_requires_distinct_metrics", 2)
            )
        )

    def _document(self) -> dict:
        document = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("rule config must be an object")
        return document

    @staticmethod
    def _rule(item: dict) -> RuleConfig:
        return RuleConfig(
            rule_id=str(item["rule_id"]),
            rule_version=int(item["rule_version"]),
            metric=HealthMetric(item["metric"]),
            comparison=Comparison(item["comparison"]),
            threshold=Decimal(str(item["threshold"])),
            minimum_sample=item.get("minimum_sample"),
            window_size_minutes=item.get("window_size_minutes"),
            lookback_windows=int(item["lookback_windows"]),
            breach_windows=int(item["breach_windows"]),
            recovery_windows=int(item["recovery_windows"]),
            health_impact=HealthImpact(item["health_impact"]),
            severity=Severity(item["severity"]),
            cooldown_seconds=int(item["cooldown_seconds"]),
            runbook_ref=str(item["runbook_ref"]),
        )


# Backward-compatible name for demo fixtures and existing callers.
JsonRuleConfigRepository = FileRuleConfigRepository
