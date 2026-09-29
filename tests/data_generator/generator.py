from collections import Counter
from typing import Any

from canonical.adapters.asia import asia_to_canonical
from canonical.adapters.india import india_to_canonical
from canonical.adapters.vn import vn_to_canonical
from canonical.domain import CanonicalTransaction

from .config import (
    ASIA_MAPPING,
    COLLECTION,
    DATABASE_ASIA_ID,
    DATABASE_ASIA_PH,
    DATABASE_INDIA,
    DATABASE_VN,
    VN_MAPPING,
    GeneratorConfig,
    ScopeSpec,
    base_case,
    scope_specs,
)
from .oracle import generate_provider_statements, reconciliation_outputs
from .source_documents import asia_document, india_document, vn_document


def _summarize(
    transactions: list[CanonicalTransaction], scenario_counts: Counter[str]
) -> dict[str, Any]:
    return {
        "total": len(transactions),
        "by_status": dict(sorted(Counter(txn.status.value for txn in transactions).items())),
        "by_type": dict(sorted(Counter(txn.txn_type.value for txn in transactions).items())),
        "by_amount_status": dict(
            sorted(Counter(txn.amount_status.value for txn in transactions).items())
        ),
        "scenarios": dict(sorted(scenario_counts.items())),
    }


def _mutation_counts(
    config: GeneratorConfig, scope: ScopeSpec, document_count: int
) -> Counter[str]:
    return Counter(
        base_case(config, scope, index).mutation for index in range(document_count)
    )


def generate_dataset(
    config: GeneratorConfig = GeneratorConfig(),
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    vn_documents = [
        vn_document(config, index) for index in range(config.records_per_region)
    ]
    india_documents = [
        india_document(config, index) for index in range(config.records_per_region)
    ]
    asia_id_count = (config.records_per_region + 1) // 2
    asia_ph_count = config.records_per_region // 2
    asia_id_documents = [
        asia_document(config, index, "asia-id") for index in range(asia_id_count)
    ]
    asia_ph_documents = [
        asia_document(config, index, "asia-ph") for index in range(asia_ph_count)
    ]

    datasets = {
        DATABASE_VN: vn_documents,
        DATABASE_ASIA_ID: asia_id_documents,
        DATABASE_ASIA_PH: asia_ph_documents,
        DATABASE_INDIA: india_documents,
    }
    scopes = scope_specs(config)
    vn_transactions = [vn_to_canonical(document, VN_MAPPING) for document in vn_documents]
    asia_transactions = [
        asia_to_canonical(document, "asia-id", ASIA_MAPPING)
        for document in asia_id_documents
    ] + [
        asia_to_canonical(document, "asia-ph", ASIA_MAPPING)
        for document in asia_ph_documents
    ]
    india_transactions = [india_to_canonical(document) for document in india_documents]
    _, reconciliation_oracle = reconciliation_outputs(datasets, config)

    ground_truth = {
        "seed": config.seed,
        "batch_id": config.batch_id,
        "records_per_region": config.records_per_region,
        "collection": COLLECTION,
        "expected_documents": {
            "VN": len(vn_documents),
            "ASIA": len(asia_id_documents) + len(asia_ph_documents),
            "INDIA": len(india_documents),
        },
        "canonical": {
            "VN": _summarize(
                vn_transactions,
                _mutation_counts(config, scopes[0], len(vn_documents)),
            ),
            "ASIA": _summarize(
                asia_transactions,
                _mutation_counts(config, scopes[1], len(asia_id_documents))
                + _mutation_counts(config, scopes[2], len(asia_ph_documents)),
            ),
            "INDIA": _summarize(
                india_transactions,
                _mutation_counts(config, scopes[3], len(india_documents)),
            ),
        },
        "reconciliation": reconciliation_oracle,
    }
    return datasets, ground_truth


__all__ = [
    "ASIA_MAPPING",
    "COLLECTION",
    "DATABASE_ASIA_ID",
    "DATABASE_ASIA_PH",
    "DATABASE_INDIA",
    "DATABASE_VN",
    "GeneratorConfig",
    "VN_MAPPING",
    "generate_dataset",
    "generate_provider_statements",
]
