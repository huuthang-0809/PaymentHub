import pytest

from canonical.application import ReplaySource
from canonical.adapters.vn import vn_to_canonical
from tests.data_generator.generator import (
    DATABASE_VN,
    VN_MAPPING,
    GeneratorConfig,
    generate_dataset,
)


def _transactions():
    datasets, _ = generate_dataset(GeneratorConfig(seed=20260923, records_per_region=5))
    return tuple(vn_to_canonical(row, VN_MAPPING) for row in datasets[DATABASE_VN])


def test_replay_keeps_order_by_default():
    rows = _transactions()
    assert tuple(ReplaySource(rows).replay()) == rows


def test_shuffle_is_repeatable_and_delay_is_injected():
    rows = _transactions()
    sleeps: list[float] = []
    first = tuple(
        ReplaySource(rows, shuffle=True, seed=7, delay_seconds=0.1, sleeper=sleeps.append).replay()
    )
    second = tuple(ReplaySource(rows, shuffle=True, seed=7).replay())
    assert first == second
    assert first != rows
    assert sleeps == [0.1] * len(rows)


def test_negative_delay_is_rejected():
    with pytest.raises(ValueError, match="negative"):
        ReplaySource((), delay_seconds=-1)
