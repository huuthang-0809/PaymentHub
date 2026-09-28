from collections import Counter
from decimal import Decimal

import pytest

from synthetic_data.generator import (
    DATABASE_ASIA_ID,
    DATABASE_ASIA_PH,
    DATABASE_INDIA,
    DATABASE_VN,
    GeneratorConfig,
    generate_dataset,
)


def test_generator_is_deterministic() -> None:
    config = GeneratorConfig(seed=20260923, records_per_region=32)
    first = generate_dataset(config)
    second = generate_dataset(config)
    assert first == second


def test_generator_creates_three_regions_and_two_asia_tenants() -> None:
    datasets, ground_truth = generate_dataset(
        GeneratorConfig(seed=20260923, records_per_region=32)
    )

    assert set(datasets) == {
        DATABASE_VN,
        DATABASE_ASIA_ID,
        DATABASE_ASIA_PH,
        DATABASE_INDIA,
    }
    assert len(datasets[DATABASE_VN]) == 32
    assert len(datasets[DATABASE_ASIA_ID]) == 16
    assert len(datasets[DATABASE_ASIA_PH]) == 16
    assert len(datasets[DATABASE_INDIA]) == 32
    assert ground_truth["expected_documents"] == {
        "VN": 32,
        "ASIA": 32,
        "INDIA": 32,
    }


def test_documents_keep_regional_source_shapes() -> None:
    datasets, _ = generate_dataset(
        GeneratorConfig(seed=20260923, records_per_region=16)
    )
    vn = datasets[DATABASE_VN][0]
    asia = datasets[DATABASE_ASIA_ID][0]
    india = datasets[DATABASE_INDIA][0]

    assert "CreatedOn" in vn and "PaymentTransactionId" in vn
    assert "CreatedAt" in asia and "AmountAsDecimal" in asia
    assert "createdAt" in india and "transactionId" in india
    assert isinstance(india["amount"], Decimal)


def test_dataset_contains_week_two_mapping_scenarios() -> None:
    _, ground_truth = generate_dataset(
        GeneratorConfig(seed=20260923, records_per_region=32)
    )
    required = {
        "pending",
        "success",
        "failed",
        "refund",
        "unknown_status",
        "invalid_amount",
        "missing_currency",
        "non_monetary",
        "duplicate_provider_id",
    }
    for region in ("VN", "ASIA", "INDIA"):
        assert required <= set(ground_truth["canonical"][region]["scenarios"])
        assert sum(ground_truth["canonical"][region]["by_status"].values()) == 32


def test_duplicate_provider_ids_are_intentional_not_duplicate_mongo_ids() -> None:
    datasets, _ = generate_dataset(
        GeneratorConfig(seed=20260923, records_per_region=64)
    )

    for database, documents in datasets.items():
        mongo_ids = [document["_id"] for document in documents]
        assert len(mongo_ids) == len(set(mongo_ids)), database

    vn_provider_ids = [
        document["PaymentTransactionId"] for document in datasets[DATABASE_VN]
    ]
    india_provider_ids = [
        document["transactionId"] for document in datasets[DATABASE_INDIA]
    ]
    asia_provider_ids = [
        document["ExternalTxnId"] for document in datasets[DATABASE_ASIA_ID]
    ]
    assert max(Counter(vn_provider_ids).values()) > 1
    assert max(Counter(india_provider_ids).values()) > 1
    assert max(Counter(asia_provider_ids).values()) > 1


@pytest.mark.parametrize(
    "config",
    [
        GeneratorConfig(seed=20260923, records_per_region=1),
        GeneratorConfig(seed=20260228, records_per_region=17),
    ],
)
def test_valid_generator_configuration(config: GeneratorConfig) -> None:
    datasets, ground_truth = generate_dataset(config)
    assert len(datasets[DATABASE_VN]) == config.records_per_region
    assert ground_truth["batch_id"] == f"seed-{config.seed}"


def test_invalid_generator_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="records_per_region must be positive"):
        GeneratorConfig(records_per_region=0)
    with pytest.raises(ValueError, match="YYYYMMDD"):
        GeneratorConfig(seed=20261340)
