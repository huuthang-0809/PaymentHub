import csv
from collections import Counter
from datetime import timedelta
from io import StringIO
from typing import Any

from shared_kernel import Region, TxnType

from .config import (
    MUTATION_CYCLE,
    STATEMENT_COLUMNS,
    BaseCase,
    GeneratorConfig,
    ProviderFixtureRow,
    ScopeSpec,
    base_case,
    scope_specs,
)


def _provider_values(case: BaseCase) -> dict[str, Any]:
    return {
        "provider": case.scope.provider,
        "region": case.scope.region.value,
        "tenant_id": case.scope.tenant_id or "",
        "provider_txn_id": case.provider_txn_id,
        "provider_ref_id": case.provider_ref_id or "",
        "merchant_ref": case.merchant_ref or "",
        "merchant_id": case.merchant_id,
        "amount_minor": case.amount_minor,
        "currency": case.scope.currency,
        "status": "SUCCESS",
        "txn_type": case.txn_type.value,
        "transaction_time": case.transaction_time.isoformat(),
    }


def _system_amount(case: BaseCase) -> int | None:
    if case.mutation == "invalid-data":
        return None
    if case.mutation == "not-reconcilable" and case.scope.region is not Region.INDIA:
        return None
    return case.amount_minor


def _normalized_provider_txn_id(
    case: BaseCase, provider_values: dict[str, Any]
) -> str | None:
    if case.scope.region is Region.ASIA and case.txn_type is TxnType.REFUND:
        return None
    value = provider_values["provider_txn_id"]
    return str(value) if value else None


def _system_provider_txn_id(case: BaseCase) -> str | None:
    if case.scope.region is Region.ASIA and case.txn_type is TxnType.REFUND:
        return None
    return case.provider_txn_id


def _system_status(case: BaseCase) -> str:
    if case.scope.region is Region.ASIA and case.txn_type is TxnType.REFUND:
        return "REFUNDED"
    if case.scope.region is Region.INDIA and case.txn_type is TxnType.TOKENIZATION:
        return "UNKNOWN"
    return "SUCCESS"


def _refund_of(case: BaseCase) -> str | None:
    if case.scope.region is Region.ASIA and case.txn_type is TxnType.REFUND:
        parent_index = (case.index // len(MUTATION_CYCLE)) * len(MUTATION_CYCLE)
        prefix = case.system_txn_id.rsplit("-", 1)[0]
        return f"{prefix}-{parent_index:06d}"
    return None


def _refunded_amount(case: BaseCase) -> int | None:
    if case.scope.region is Region.INDIA and case.mutation != "invalid-data":
        return 0
    return None


def _expected_system_only(
    case: BaseCase, result_type: str, reason: str
) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "result_type": result_type,
        "region": case.scope.region.value,
        "tenant_id": case.scope.tenant_id,
        "provider": case.scope.provider,
        "txn_type": case.txn_type.value,
        "system_txn_id": case.system_txn_id,
        "system_provider_txn_id": _system_provider_txn_id(case),
        "system_status": _system_status(case),
        "system_amount_minor": _system_amount(case),
        "system_currency": case.scope.currency,
        "refund_of": _refund_of(case),
        "refunded_amount_minor": _refunded_amount(case),
        "provider_txn_id": None,
        "provider_amount_minor": None,
        "provider_currency": None,
        "source_file": None,
        "source_row_number": None,
        "delta_amount": None,
        "reason": reason,
    }


def _expected_provider_only(
    case_id: str,
    scope: ScopeSpec,
    provider_values: dict[str, Any],
    fixture_id: str,
    result_type: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "result_type": result_type,
        "region": scope.region.value,
        "tenant_id": scope.tenant_id,
        "provider": scope.provider,
        "txn_type": provider_values["txn_type"],
        "system_txn_id": None,
        "system_provider_txn_id": None,
        "system_status": None,
        "system_amount_minor": None,
        "system_currency": None,
        "refund_of": None,
        "refunded_amount_minor": None,
        "provider_txn_id": provider_values["provider_txn_id"] or None,
        "provider_amount_minor": provider_values["amount_minor"],
        "provider_currency": provider_values["currency"],
        "source_file": scope.filename,
        "source_row_number": None,
        "delta_amount": None,
        "reason": reason,
        "_provider_fixture_id": fixture_id,
    }


def _expected_pair(
    case: BaseCase,
    provider_values: dict[str, Any],
    fixture_id: str,
    result_type: str,
    matched_by: str,
) -> dict[str, Any]:
    same_currency = case.scope.currency == provider_values["currency"]
    system_amount = _system_amount(case)
    provider_amount = provider_values["amount_minor"]
    delta = (
        system_amount - provider_amount
        if same_currency and system_amount is not None
        else None
    )
    return {
        "case_id": case.case_id,
        "result_type": result_type,
        "region": case.scope.region.value,
        "tenant_id": case.scope.tenant_id,
        "provider": case.scope.provider,
        "txn_type": case.txn_type.value,
        "system_txn_id": case.system_txn_id,
        "system_provider_txn_id": _system_provider_txn_id(case),
        "system_status": _system_status(case),
        "system_amount_minor": system_amount,
        "system_currency": case.scope.currency,
        "refund_of": _refund_of(case),
        "refunded_amount_minor": _refunded_amount(case),
        "provider_txn_id": _normalized_provider_txn_id(case, provider_values),
        "provider_amount_minor": provider_amount,
        "provider_currency": provider_values["currency"],
        "source_file": case.scope.filename,
        "source_row_number": None,
        "delta_amount": delta,
        "reason": f"{result_type}: matched by {matched_by}",
        "_provider_fixture_id": fixture_id,
    }


def _paired_fixture(
    case: BaseCase,
) -> tuple[list[ProviderFixtureRow], list[dict[str, Any]]]:
    scope = case.scope
    values = _provider_values(case)
    result_type = "MATCHED"
    matched_by = "provider_txn_id"
    if case.mutation == "amount-mismatch":
        values["amount_minor"] = case.amount_minor - 1
        result_type = "AMOUNT_MISMATCH"
    elif case.mutation == "currency-mismatch":
        values["currency"] = "USD"
        result_type = "CURRENCY_MISMATCH"
    elif case.mutation == "fuzzy-match":
        values["provider_txn_id"] = f"fuzzy-provider-{scope.slug}-{case.index:06d}"
        values["provider_ref_id"] = f"fuzzy-ref-{scope.slug}-{case.index:06d}"
        values["merchant_ref"] = f"fuzzy-merchant-ref-{scope.slug}-{case.index:06d}"
        result_type = "FUZZY_MATCHED"
        matched_by = "composite"
    elif scope.region is Region.ASIA and case.txn_type is TxnType.REFUND:
        matched_by = "provider_ref_id"

    fixture_id = f"{scope.slug}:{case.case_id}:primary"
    rows = [ProviderFixtureRow(fixture_id, case.case_id, values)]
    expected = [_expected_pair(case, values, fixture_id, result_type, matched_by)]
    if case.mutation != "duplicate":
        return rows, expected

    duplicate_values = dict(values)
    duplicate_values["transaction_time"] = (
        case.transaction_time + timedelta(seconds=1)
    ).isoformat()
    duplicate_id = f"{scope.slug}:{case.case_id}:duplicate"
    rows.append(ProviderFixtureRow(duplicate_id, case.case_id, duplicate_values))
    normalized_values = dict(duplicate_values)
    normalized_values["provider_txn_id"] = _normalized_provider_txn_id(
        case, duplicate_values
    )
    expected.append(
        _expected_provider_only(
            case.case_id,
            scope,
            normalized_values,
            duplicate_id,
            "DUPLICATE",
            "Repeated provider identifier within currency scope",
        )
    )
    return rows, expected


def _provider_only_fixture(
    config: GeneratorConfig, scope: ScopeSpec, cycle: int
) -> tuple[ProviderFixtureRow, dict[str, Any]]:
    case_id = f"missing-in-system-{cycle:04d}"
    fixture_id = f"{scope.slug}:{case_id}:provider-only"
    values = {
        "provider": scope.provider,
        "region": scope.region.value,
        "tenant_id": scope.tenant_id or "",
        "provider_txn_id": (
            f"{scope.provider.lower()}-provider-only-{config.seed}-{cycle:04d}"
        ),
        "provider_ref_id": (
            f"{scope.slug}-provider-only-ref-{config.seed}-{cycle:04d}"
            if scope.region is Region.ASIA
            else ""
        ),
        "merchant_ref": f"{scope.slug}-provider-only-{config.seed}-{cycle:04d}",
        "merchant_id": f"{scope.slug}-provider-only-merchant-{cycle:04d}",
        "amount_minor": 200_000 + cycle,
        "currency": scope.currency,
        "status": "SUCCESS",
        "txn_type": TxnType.PAYMENT.value,
        "transaction_time": (
            config.base_time + timedelta(hours=12, seconds=cycle)
        ).isoformat(),
    }
    row = ProviderFixtureRow(fixture_id, case_id, values)
    expected = _expected_provider_only(
        case_id,
        scope,
        values,
        fixture_id,
        "MISSING_IN_SYSTEM",
        "No eligible system counterpart found",
    )
    return row, expected


def _build_scope_fixtures(
    config: GeneratorConfig, scope: ScopeSpec, document_count: int
) -> tuple[list[ProviderFixtureRow], list[dict[str, Any]]]:
    rows: list[ProviderFixtureRow] = []
    expected: list[dict[str, Any]] = []
    system_only = {
        "missing-in-provider": (
            "MISSING_IN_PROVIDER",
            "No eligible provider counterpart found",
        ),
        "not-reconcilable": (
            "NOT_RECONCILABLE",
            "Transaction is outside reconciliation scope",
        ),
        "invalid-data": ("INVALID_DATA", "amount_status=INVALID"),
    }

    for index in range(document_count):
        case = base_case(config, scope, index)
        if case.mutation in system_only:
            result_type, reason = system_only[case.mutation]
            expected.append(_expected_system_only(case, result_type, reason))
        else:
            case_rows, case_expected = _paired_fixture(case)
            rows.extend(case_rows)
            expected.extend(case_expected)

        if index % len(MUTATION_CYCLE) == len(MUTATION_CYCLE) - 1:
            row, result = _provider_only_fixture(
                config, scope, index // len(MUTATION_CYCLE)
            )
            rows.append(row)
            expected.append(result)

    row_numbers = {row.fixture_id: number for number, row in enumerate(rows, start=2)}
    for result in expected:
        fixture_id = result.pop("_provider_fixture_id", None)
        if fixture_id is not None:
            result["source_row_number"] = row_numbers[fixture_id]
    return rows, expected


def _statement_csv(rows: list[ProviderFixtureRow]) -> str:
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=STATEMENT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(row.values for row in rows)
    return output.getvalue()


def reconciliation_outputs(
    datasets: dict[str, list[dict[str, Any]]], config: GeneratorConfig
) -> tuple[dict[str, str], dict[str, Any]]:
    statements: dict[str, str] = {}
    oracle: dict[str, Any] = {}
    for scope in scope_specs(config):
        rows, expected = _build_scope_fixtures(
            config, scope, len(datasets[scope.database])
        )
        statements[scope.filename] = _statement_csv(rows)
        oracle[scope.filename] = {
            "scope": {
                "region": scope.region.value,
                "tenant_id": scope.tenant_id,
                "provider": scope.provider,
            },
            "system_rows": len(datasets[scope.database]),
            "provider_rows": len(rows),
            "expected_summary": dict(
                sorted(Counter(item["result_type"] for item in expected).items())
            ),
            "expected_results": expected,
        }
    return statements, oracle


def generate_provider_statements(
    datasets: dict[str, list[dict[str, Any]]],
    config: GeneratorConfig = GeneratorConfig(),
) -> dict[str, str]:
    """Build independent provider fixtures for each reconciliation scope."""
    statements, _ = reconciliation_outputs(datasets, config)
    return statements
