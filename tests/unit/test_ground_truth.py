import pytest

from tests.data_generator.generator import GeneratorConfig, generate_dataset
from tests.data_generator.ground_truth import assert_matches_ground_truth


def test_all_regions_match_ground_truth_by_status():
    datasets, truth = generate_dataset(
        GeneratorConfig(seed=20260923, records_per_region=32)
    )
    assert_matches_ground_truth(datasets, truth)


def test_mismatch_is_reported():
    datasets, truth = generate_dataset(
        GeneratorConfig(seed=20260923, records_per_region=16)
    )
    truth["canonical"]["VN"]["by_status"] = {"SUCCESS": 999}
    with pytest.raises(AssertionError, match="canonical status mismatch"):
        assert_matches_ground_truth(datasets, truth)
