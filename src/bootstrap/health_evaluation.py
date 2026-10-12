"""Run one bounded provider-health evaluation cycle."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from monitoring.adapters.health_config import JsonRuleConfigRepository
from monitoring.adapters.postgres_health import (
    PostgresAlertUnitOfWork,
    PostgresHealthMetricSource,
    PostgresHealthRunnerStatusRepository,
    PostgresHealthScopeRepository,
)
from monitoring.application import EvaluateProviderHealth, HealthEvaluationOutput
from monitoring.domain import AlertRecord, AlertTransition, EvaluationScope
from shared_kernel import Region, parse_iso_utc

DEFAULT_RULES = Path(__file__).resolve().parents[2] / "config" / "health-rules.demo.json"


class _StatusRepository(Protocol):
    def record_success(
        self,
        scope: EvaluationScope,
        *,
        run_at: datetime,
        data_as_of: datetime | None,
        freshness_sla_seconds: int,
        summary: dict[str, Any],
    ) -> None: ...

    def record_failure(
        self,
        scope: EvaluationScope,
        *,
        run_at: datetime,
        freshness_sla_seconds: int,
        error: BaseException,
    ) -> None: ...


class NullNotificationPort:
    def send(self, alert: AlertRecord, transition: AlertTransition) -> None:
        return None


def _scope(value: str) -> EvaluationScope:
    parts = value.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            "scope must use REGION:tenant_scope:provider"
        )
    region, tenant_scope, provider = (item.strip() for item in parts)
    try:
        return EvaluationScope(Region(region.upper()), tenant_scope, provider.upper())
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _environment_scopes() -> list[EvaluationScope]:
    value = os.getenv("HEALTH_SCOPES", "")
    return [_scope(item) for item in value.split(",") if item.strip()]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate provider health once from committed closed windows"
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv(
            "DATABASE_URL",
            "postgresql://paymenthub:paymenthub@localhost:5432/paymenthub",
        ),
    )
    parser.add_argument(
        "--rules-file",
        type=Path,
        default=Path(os.getenv("HEALTH_RULES_FILE", str(DEFAULT_RULES))),
    )
    parser.add_argument("--scope", action="append", type=_scope, default=None)
    parser.add_argument("--as-of", default=os.getenv("HEALTH_AS_OF"))
    parser.add_argument(
        "--freshness-sla-seconds",
        type=int,
        default=int(os.getenv("HEALTH_FRESHNESS_SLA_SECONDS", "600")),
    )
    return parser


def run_evaluation_cycle(
    scopes: tuple[EvaluationScope, ...],
    *,
    as_of: datetime,
    freshness_sla_seconds: int,
    evaluate,
    status_repository: _StatusRepository,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "as_of": as_of.isoformat().replace("+00:00", "Z"),
        "status": "COMPLETED",
        "evaluated_scopes": 0,
        "evaluated_rules": 0,
        "opened": 0,
        "updated": 0,
        "resolved": 0,
        "skipped": 0,
        "notification_failures": 0,
        "failures": [],
        "scopes": [],
    }
    for scope in scopes:
        scope_name = {
            "region": scope.region.value,
            "tenant_scope": scope.tenant_scope,
            "provider": scope.provider,
        }
        try:
            output: HealthEvaluationOutput = evaluate(scope, as_of)
            actions = Counter(item.action.value for item in output.transitions)
            scope_summary = {
                **scope_name,
                "health_status": output.evaluation.status.value,
                "rule_count": len(output.evaluation.evaluations),
                "data_as_of": (
                    output.evaluation.data_as_of.isoformat().replace("+00:00", "Z")
                    if output.evaluation.data_as_of is not None
                    else None
                ),
                "opened": actions["OPEN"],
                "updated": actions["UPDATE"],
                "resolved": actions["RESOLVE"],
                "replayed": output.replayed,
                "notification_failures": output.notification_failures,
            }
            status_repository.record_success(
                scope,
                run_at=as_of,
                data_as_of=output.evaluation.data_as_of,
                freshness_sla_seconds=freshness_sla_seconds,
                summary=scope_summary,
            )
            summary["evaluated_scopes"] += 1
            summary["evaluated_rules"] += len(output.evaluation.evaluations)
            summary["opened"] += actions["OPEN"]
            summary["updated"] += actions["UPDATE"]
            summary["resolved"] += actions["RESOLVE"]
            summary["skipped"] += int(output.replayed)
            summary["notification_failures"] += output.notification_failures
            summary["scopes"].append(scope_summary)
        except Exception as error:
            try:
                status_repository.record_failure(
                    scope,
                    run_at=as_of,
                    freshness_sla_seconds=freshness_sla_seconds,
                    error=error,
                )
            except Exception:
                pass
            summary["failures"].append(
                {
                    **scope_name,
                    "error_code": type(error).__name__,
                    "error_message": str(error)[:500],
                }
            )

    if summary["failures"] or summary["notification_failures"]:
        summary["status"] = "FAILED"
    return summary


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.freshness_sla_seconds < 0:
        raise SystemExit("--freshness-sla-seconds cannot be negative")
    as_of = parse_iso_utc(args.as_of) if args.as_of else datetime.now(UTC)
    scopes = tuple(args.scope or _environment_scopes())
    if not scopes:
        scopes = PostgresHealthScopeRepository(args.database_url).list_scopes()
    if not scopes:
        raise SystemExit("no health scopes found; provide --scope or HEALTH_SCOPES")
    if len(set(scopes)) != len(scopes):
        raise SystemExit("health scopes must be unique")

    config_repository = JsonRuleConfigRepository(args.rules_file)
    metric_source = PostgresHealthMetricSource(args.database_url)
    status_repository = PostgresHealthRunnerStatusRepository(args.database_url)

    def evaluate(scope: EvaluationScope, evaluated_at: datetime):
        return EvaluateProviderHealth(
            metric_source,
            config_repository,
            PostgresAlertUnitOfWork(args.database_url),
            NullNotificationPort(),
        ).execute(scope, evaluated_at=evaluated_at)

    summary = run_evaluation_cycle(
        scopes,
        as_of=as_of,
        freshness_sla_seconds=args.freshness_sla_seconds,
        evaluate=evaluate,
        status_repository=status_repository,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
