import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from canonical.adapters.asia import asia_to_canonical
from canonical.adapters.india import india_to_canonical
from canonical.adapters.vn import vn_to_canonical
from canonical.domain import CanonicalTransaction
from shared_kernel import Region

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
from .oracle import (
    generate_provider_statements,
    iter_scope_expected_results,
    reconciliation_outputs,
    scope_oracle_summary,
)
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


def scope_document_count(config: GeneratorConfig, scope: ScopeSpec) -> int:
    if scope.database in {DATABASE_ASIA_ID, DATABASE_ASIA_PH}:
        if scope.database == DATABASE_ASIA_ID:
            return (config.records_per_region + 1) // 2
        return config.records_per_region // 2
    return config.records_per_region


def iter_scope_documents(
    config: GeneratorConfig, scope: ScopeSpec
) -> Iterator[dict[str, Any]]:
    """Generate one source document at a time for a single reconciliation scope."""
    count = scope_document_count(config, scope)
    for index in range(count):
        if scope.database == DATABASE_VN:
            yield vn_document(config, index)
        elif scope.database == DATABASE_INDIA:
            yield india_document(config, index)
        else:
            if scope.tenant_id is None:
                raise ValueError("Asia scope requires tenant_id")
            yield asia_document(config, index, scope.tenant_id)


def _streaming_canonical_summary(
    config: GeneratorConfig, scope: ScopeSpec
) -> dict[str, Any]:
    by_status: Counter[str] = Counter()
    by_type: Counter[str] = Counter()
    by_amount_status: Counter[str] = Counter()
    scenarios: Counter[str] = Counter()
    total = 0
    for index, document in enumerate(iter_scope_documents(config, scope)):
        if scope.database == DATABASE_VN:
            transaction = vn_to_canonical(document, VN_MAPPING)
        elif scope.database == DATABASE_INDIA:
            transaction = india_to_canonical(document)
        else:
            assert scope.tenant_id is not None
            transaction = asia_to_canonical(document, scope.tenant_id, ASIA_MAPPING)
        total += 1
        by_status[transaction.status.value] += 1
        by_type[transaction.txn_type.value] += 1
        by_amount_status[transaction.amount_status.value] += 1
        scenarios[base_case(config, scope, index).mutation] += 1
    return {
        "total": total,
        "by_status": dict(sorted(by_status.items())),
        "by_type": dict(sorted(by_type.items())),
        "by_amount_status": dict(sorted(by_amount_status.items())),
        "scenarios": dict(sorted(scenarios.items())),
    }


def write_streaming_ground_truth(
    path: Path,
    config: GeneratorConfig,
    provider_statement_rows: dict[str, int] | None = None,
) -> None:
    """Write the exact oracle incrementally, including large expected result sets."""
    scopes = scope_specs(config)
    scope_summaries = {
        scope.database: _streaming_canonical_summary(config, scope)
        for scope in scopes
    }
    asia_summary: dict[str, Any] = {
        "total": sum(
            scope_summaries[scope.database]["total"]
            for scope in scopes if scope.region is Region.ASIA
        )
    }
    for key in ("by_status", "by_type", "by_amount_status", "scenarios"):
        combined: Counter[str] = Counter()
        for scope in scopes:
            if scope.region is Region.ASIA:
                combined.update(scope_summaries[scope.database][key])
        asia_summary[key] = dict(sorted(combined.items()))

    header = {
        "seed": config.seed,
        "batch_id": config.batch_id,
        "records_per_region": config.records_per_region,
        "collection": COLLECTION,
        "expected_documents": {
            "VN": config.records_per_region,
            "ASIA": config.records_per_region,
            "INDIA": config.records_per_region,
        },
        "canonical": {
            "VN": scope_summaries[DATABASE_VN],
            "ASIA": asia_summary,
            "INDIA": scope_summaries[DATABASE_INDIA],
        },
    }
    if provider_statement_rows is not None:
        header["provider_statements"] = provider_statement_rows
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded_header = json.dumps(header, indent=2, sort_keys=True)
    with path.open("w", encoding="utf-8") as output:
        output.write(encoded_header[:-2])
        output.write(',\n  "reconciliation": {\n')
        for scope_index, scope in enumerate(scopes):
            count = scope_document_count(config, scope)
            provider_rows, expected_summary = scope_oracle_summary(
                config, scope, count
            )
            if scope_index:
                output.write(",\n")
            output.write(f"    {json.dumps(scope.filename)}: {{\n")
            output.write(
                '      "scope": '
                + json.dumps({
                    "region": scope.region.value,
                    "tenant_id": scope.tenant_id,
                    "provider": scope.provider,
                }, sort_keys=True)
                + ",\n"
            )
            output.write(f'      "system_rows": {count},\n')
            output.write(f'      "provider_rows": {provider_rows},\n')
            output.write(
                '      "expected_summary": '
                + json.dumps(expected_summary, sort_keys=True)
                + ',\n      "expected_results": [\n'
            )
            for result_index, result in enumerate(
                iter_scope_expected_results(config, scope, count)
            ):
                if result_index:
                    output.write(",\n")
                output.write("        " + json.dumps(result, sort_keys=True))
            output.write("\n      ]\n    }")
        output.write("\n  }\n}\n")


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
    "iter_scope_documents",
    "scope_document_count",
    "write_streaming_ground_truth",
]
