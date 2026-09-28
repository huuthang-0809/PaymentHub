from __future__ import annotations

from typing import Mapping

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, REGISTRY

from shared_kernel import Region


class ReconciliationMetrics:
    """Prometheus instrumentation with bounded, operational labels."""

    def __init__(self, registry: CollectorRegistry = REGISTRY) -> None: # registry: registry metrics, REGISTRY: defaut Registry
        labels = ("region", "tenant_id", "provider")
        self.requests = Counter(
            "paymenthub_reconciliation_requests_total",
            "Reconciliation requests by terminal outcome.",
            (*labels, "outcome"), # outcome: runs result (completed, replayed, invalid_statement, in_progress, failed)
            registry=registry,
        )
        self.duration = Histogram(
            "paymenthub_reconciliation_duration_seconds",
            "End-to-end reconciliation request duration.",
            (*labels, "outcome"),
            buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
            registry=registry,
        )
        self.input_rows = Counter(
            "paymenthub_reconciliation_input_rows_total",
            "Rows processed by completed, non-replayed reconciliations.",
            (*labels, "side"), # side: system or provider
            registry=registry,
        )
        self.results = Counter(
            "paymenthub_reconciliation_results_total",
            "Results produced by completed, non-replayed reconciliations.",
            (*labels, "result"), # result (MATCHED, ...)
            registry=registry,
        )
        self.last_success = Gauge( # Timestamp of last success run.
            "paymenthub_reconciliation_last_success_timestamp_seconds",
            "Unix timestamp of the last completed, non-replayed reconciliation.",
            labels,
            registry=registry,
        )
        self.dependency_duration = Histogram( # duration of get system source, save complete run and fail run.
            "paymenthub_reconciliation_dependency_duration_seconds",
            "Reconciliation dependency operation duration.",
            (*labels, "dependency", "operation", "outcome"),
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
            registry=registry,
        )

    @staticmethod
    def _scope(
        region: Region, tenant_id: str | None, provider: str
    ) -> dict[str, str]:
        return {
            "region": region.value,
            "tenant_id": tenant_id or "none",
            "provider": provider.strip().upper(),
        }

    def record_dependency(
        self,
        *,
        dependency: str,
        operation: str,
        region: Region,
        tenant_id: str | None,
        provider: str,
        outcome: str,
        duration_seconds: float,
    ) -> None:
        labels = self._scope(region, tenant_id, provider)
        self.dependency_duration.labels( #choose series for scope/operation/outcome
            **labels,
            dependency=dependency,
            operation=operation,
            outcome=outcome,
        ).observe(duration_seconds)

    def record_run(
        self,
        *,
        region: Region,
        tenant_id: str | None,
        provider: str,
        outcome: str,
        duration_seconds: float,
        system_rows: int | None = None,
        provider_rows: int | None = None,
        summary: Mapping[str, int] | None = None,
    ) -> None:
        labels = self._scope(region, tenant_id, provider)
        self.requests.labels(**labels, outcome=outcome).inc()
        self.duration.labels(**labels, outcome=outcome).observe(duration_seconds)
        if outcome != "completed":
            return
        self.input_rows.labels(**labels, side="system").inc(system_rows or 0)
        self.input_rows.labels(**labels, side="provider").inc(provider_rows or 0)
        for result, count in (summary or {}).items():
            self.results.labels(**labels, result=result).inc(count)
        self.last_success.labels(**labels).set_to_current_time()
