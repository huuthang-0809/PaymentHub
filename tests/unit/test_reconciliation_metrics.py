from prometheus_client import CollectorRegistry, generate_latest

from reconciliation.adapters.metrics import ReconciliationMetrics
from shared_kernel import Region


def samples(registry: CollectorRegistry) -> str:
    return generate_latest(registry).decode("utf-8")


def test_completed_run_records_counts_and_scope() -> None:
    registry = CollectorRegistry()
    metrics = ReconciliationMetrics(registry)

    metrics.record_run(
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
    assert 'paymenthub_reconciliation_latest_provider_rows{provider="ONEPAY",region="VN",tenant_id="none"} 2.0' in text
    assert 'paymenthub_reconciliation_latest_missing_in_system_rows{provider="ONEPAY",region="VN",tenant_id="none"} 0.0' in text
    assert 'paymenthub_reconciliation_latest_currency_delta_minor{currency="VND",provider="ONEPAY",region="VN",tenant_id="none"} 500.0' in text


def test_replay_is_counted_without_recounting_business_results() -> None:
    registry = CollectorRegistry()
    metrics = ReconciliationMetrics(registry)

    metrics.record_run(
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
        region=Region.INDIA,
        tenant_id=None,
        provider="RAZORPAY",
        outcome="invalid_statement",
        duration_seconds=0.02,
    )

    text = samples(registry)
    assert 'outcome="invalid_statement"' in text
    assert 'paymenthub_reconciliation_duration_seconds_count{outcome="invalid_statement"' in text


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
