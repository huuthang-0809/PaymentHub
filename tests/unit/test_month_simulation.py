from datetime import date

import pytest

from tests.data_generator.month_simulation import (
    missing_statement_targets,
    simulation_dates,
)


def test_simulation_dates_builds_consecutive_virtual_days() -> None:
    assert simulation_dates(date(2026, 9, 29), 3) == (
        date(2026, 9, 29),
        date(2026, 9, 30),
        date(2026, 10, 1),
    )


def test_simulation_days_must_be_positive() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        simulation_dates(date(2026, 9, 1), 0)


def test_two_missing_statements_are_spread_across_simulation() -> None:
    dates = simulation_dates(date(2026, 9, 1), 30)

    assert missing_statement_targets(dates, 2) == {
        date(2026, 9, 10): 0,
        date(2026, 9, 20): 1,
    }


def test_one_missing_statement_is_near_middle_of_simulation() -> None:
    dates = simulation_dates(date(2026, 9, 1), 30)

    assert missing_statement_targets(dates, 1) == {
        date(2026, 9, 15): 0,
    }


@pytest.mark.parametrize("count", [-1, 3])
def test_missing_statement_count_must_be_between_zero_and_two(count: int) -> None:
    with pytest.raises(ValueError, match="between 0 and 2"):
        missing_statement_targets((date(2026, 9, 1),), count)


def test_missing_statement_count_cannot_exceed_simulation_days() -> None:
    with pytest.raises(ValueError, match="must not exceed SIMULATION_DAYS"):
        missing_statement_targets((date(2026, 9, 1),), 2)
