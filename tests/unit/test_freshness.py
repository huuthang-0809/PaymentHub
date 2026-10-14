from monitoring.domain import FreshnessStatus, classify_source_freshness


def test_live_heartbeat_distinguishes_idle_from_stale_data() -> None:
    assert classify_source_freshness(
        event_lag_seconds=35 * 60,
        heartbeat_lag_seconds=10,
        sla_seconds=5 * 60,
    ) is FreshnessStatus.IDLE


def test_missing_heartbeat_marks_source_unavailable() -> None:
    assert classify_source_freshness(
        event_lag_seconds=35 * 60,
        heartbeat_lag_seconds=35 * 60,
        sla_seconds=5 * 60,
    ) is FreshnessStatus.UNAVAILABLE


def test_legacy_source_without_heartbeat_keeps_stale_semantics() -> None:
    assert classify_source_freshness(
        event_lag_seconds=35 * 60,
        heartbeat_lag_seconds=None,
        sla_seconds=5 * 60,
    ) is FreshnessStatus.STALE
