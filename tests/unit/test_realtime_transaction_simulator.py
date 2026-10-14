from datetime import UTC, datetime

import pytest

from bootstrap.realtime_transaction_simulator import ScenarioGenerator


def test_scenario_generator_builds_a_valid_terminal_lifecycle() -> None:
    generator = ScenarioGenerator(
        seed=7,
        failure_rate=0,
        stuck_rate=0,
        min_terminal_delay=6,
        max_terminal_delay=6,
    )

    events = generator.transaction(1, datetime(2026, 10, 13, tzinfo=UTC))

    assert [item.payload["status"] for item in events] == [
        "CREATED",
        "PENDING",
        "SUCCESS",
    ]
    assert len({item.payload["event_id"] for item in events}) == 3
    assert len({item.payload["txn_id"] for item in events}) == 1
    assert events[-1].payload["is_terminal"] is True
    assert events[0].payload["raw"]["simulator"] == "paymenthub-realtime-v1"


def test_scenario_generator_can_leave_a_transaction_pending() -> None:
    generator = ScenarioGenerator(
        seed=1,
        failure_rate=0,
        stuck_rate=1,
        min_terminal_delay=2,
        max_terminal_delay=2,
    )

    events = generator.transaction(1, datetime(2026, 10, 13, tzinfo=UTC))

    assert [item.payload["status"] for item in events] == ["CREATED", "PENDING"]
    assert all(item.payload["is_terminal"] is False for item in events)


@pytest.mark.parametrize(
    ("failure_rate", "stuck_rate"),
    [(-0.1, 0), (1.1, 0), (0, -0.1), (0, 1.1)],
)
def test_scenario_generator_rejects_invalid_rates(
    failure_rate: float, stuck_rate: float
) -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        ScenarioGenerator(
            seed=1,
            failure_rate=failure_rate,
            stuck_rate=stuck_rate,
            min_terminal_delay=1,
            max_terminal_delay=2,
        )
