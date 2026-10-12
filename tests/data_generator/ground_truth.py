from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from canonical.adapters.asia import AsiaMapping, asia_to_canonical
from canonical.adapters.india import india_to_canonical
from canonical.adapters.vn import VNMapping, vn_to_canonical
from canonical.domain import CanonicalTransaction

from .generator import (
    ASIA_MAPPING,
    DATABASE_ASIA_ID,
    DATABASE_ASIA_PH,
    DATABASE_INDIA,
    DATABASE_VN,
    VN_MAPPING,
)


def canonical_status_counts(
    datasets: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    vn_mapping: VNMapping = VN_MAPPING,
    asia_mapping: AsiaMapping = ASIA_MAPPING,
) -> dict[str, dict[str, int]]:
    regional: dict[str, list[CanonicalTransaction]] = {
        "VN": [vn_to_canonical(row, vn_mapping) for row in datasets[DATABASE_VN]],
        "ASIA": [
            asia_to_canonical(row, "asia-id", asia_mapping)
            for row in datasets[DATABASE_ASIA_ID]
        ]
        + [
            asia_to_canonical(row, "asia-ph", asia_mapping)
            for row in datasets[DATABASE_ASIA_PH]
        ],
        "INDIA": [india_to_canonical(row) for row in datasets[DATABASE_INDIA]],
    }
    return {
        region: dict(sorted(Counter(row.status.value for row in rows).items()))
        for region, rows in regional.items()
    }


def assert_matches_ground_truth(
    datasets: Mapping[str, Sequence[Mapping[str, Any]]],
    ground_truth: Mapping[str, Any],
) -> None:
    actual = canonical_status_counts(datasets)
    expected = {
        region: summary["by_status"]
        for region, summary in ground_truth["canonical"].items()
    }
    if actual != expected:
        raise AssertionError(f"canonical status mismatch: actual={actual}, expected={expected}")
