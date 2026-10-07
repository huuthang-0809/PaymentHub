from datetime import date
from uuid import UUID

from prometheus_client import CollectorRegistry, generate_latest

from reconciliation.adapters.metrics import ReconciliationMetrics
from shared_kernel import Region


def samples(registry: CollectorRegistry) -> str:
    return generate_latest(registry).decode("utf-8")


def test_completed_run_records_counts_and_scope() -> None:
    registry = CollectorRegistry()
    metrics = ReconciliationMetrics(registry)

    metrics.record_run(
        run_id=UUID("00000000-0000-0000-0000-000000000001"),
        business_date=date(2026, 9, 22),
        region=Region.VN,
        tenant_id=None,
        provider=" onepay ",
        outcome="completed",
        duration_seconds=1.5,
        system_rows=3,
        provider_rows=2,
        summary={"MATCHED": 2, "MISSING_IN_PROVIDER": 1},
        currency_deltas={"VND": 500},
    )

    text = samples(registry)
    assert 'paymenthub_reconciliation_requests_total{outcome="completed",provider="ONEPAY",region="VN",tenant_id="none"} 1.0' in text
    assert 'paymenthub_reconciliation_input_rows_total{provider="ONEPAY",region="VN",side="system",tenant_id="none"} 3.0' in text
    assert 'paymenthub_reconciliation_results_total{provider="ONEPAY",region="VN",result="MATCHED",tenant_id="none"} 2.0' in text
    assert "paymenthub_reconciliation_last_success_timestamp_seconds" in text
    assert 'business_date="2026-09-22"' in text
    assert 'run_id="00000000-0000-0000-0000-000000000001"' in text
    assert 'paymenthub_reconciliation_run_provider_rows{' in text
    assert 'paymenthub_reconciliation_run_missing_in_system_rows{' in text
    assert 'paymenthub_reconciliation_run_currency_delta_minor{' in text


def test_replay_is_counted_without_recounting_business_results() -> None:
    registry = CollectorRegistry()
    metrics = ReconciliationMetrics(registry)

    metrics.record_run(
        run_id=UUID("00000000-0000-0000-0000-000000000001"),
        business_date=date(2026, 9, 22),
        region=Region.ASIA,
        tenant_id="asia-id",
        provider="ESPAY",
        outcome="replayed",
        duration_seconds=0.01,
    )

    text = samples(registry)
    assert 'outcome="replayed"' in text
    assert "paymenthub_reconciliation_input_rows_total{" not in text
    assert "paymenthub_reconciliation_results_total{" not in text
    assert "paymenthub_reconciliation_last_success_timestamp_seconds{" not in text


def test_invalid_statement_records_bounded_failure_outcome() -> None:
    registry = CollectorRegistry()
    metrics = ReconciliationMetrics(registry)

    metrics.record_run(
        run_id=None,
        business_date=date(2026, 9, 22),
        region=Region.INDIA,
        tenant_id=None,
        provider="RAZORPAY",
        outcome="invalid_statement",
        duration_seconds=0.02,
    )

    text = samples(registry)
    assert 'outcome="invalid_statement"' in text
    assert 'paymenthub_reconciliation_duration_seconds_count{outcome="invalid_statement"' in text


def test_run_metrics_keep_previous_unresolved_run_correlation() -> None:
    registry = CollectorRegistry()
    metrics = ReconciliationMetrics(registry)

    for day in (22, 23):
        metrics.record_run(
            run_id=UUID(f"00000000-0000-0000-0000-{day:012d}"),
            business_date=date(2026, 9, day),
            region=Region.VN,
            tenant_id=None,
            provider="ONEPAY",
            outcome="completed",
            duration_seconds=1,
            provider_rows=100,
            summary={"MISSING_IN_SYSTEM": 10},
            currency_deltas={"VND": 200_000},
        )

    text = samples(registry)
    assert 'business_date="2026-09-23"' in text
    assert 'run_id="00000000-0000-0000-0000-000000000023"' in text
    assert 'business_date="2026-09-22"' in text
    assert 'run_id="00000000-0000-0000-0000-000000000022"' in text


def test_dependency_latency_records_operation_and_failure() -> None:
    registry = CollectorRegistry()
    metrics = ReconciliationMetrics(registry)

    metrics.record_dependency(
        dependency="system_source",
        operation="get_transactions",
        region=Region.ASIA,
        tenant_id="asia-ph",
        provider="2C2P",
        outcome="failed",
        duration_seconds=5.0,
    )

    text = samples(registry)
    assert 'dependency="system_source"' in text
    assert 'operation="get_transactions"' in text
    assert 'outcome="failed"' in text
