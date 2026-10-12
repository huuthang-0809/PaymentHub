import json
from collections import Counter
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from canonical.adapters.asia import asia_to_canonical
from canonical.adapters.india import india_to_canonical
from canonical.adapters.vn import vn_to_canonical
from reconciliation.adapters import CsvFixtureParser
from reconciliation.application.calendar import resolve_business_period
from reconciliation.application.summary import build_financial_summary
from reconciliation.domain import MatchingEngine, ReconResultType
from tests.data_generator.generator import (
    ASIA_MAPPING,
    DATABASE_ASIA_ID,
    DATABASE_ASIA_PH,
    DATABASE_INDIA,
    DATABASE_VN,
    GeneratorConfig,
    VN_MAPPING,
    generate_dataset,
    generate_provider_statements,
    write_streaming_ground_truth,
)
from tests.data_generator.config import base_case, scope_document_count, scope_specs
from tests.data_generator.oracle import write_provider_statement


def test_generator_is_deterministic() -> None:
    config = GeneratorConfig(seed=20260923, records_per_region=32)
    first = generate_dataset(config)
    second = generate_dataset(config)
    assert first == second


def test_monitoring_rich_profile_spreads_time_values_and_statuses() -> None:
    end_at = datetime(2026, 10, 6, 8, tzinfo=UTC)
    config = GeneratorConfig(
        seed=20261006,
        records_per_region=1000,
        profile="monitoring-rich",
        time_span_hours=12,
        end_at=end_at,
    )
    datasets, ground_truth = generate_dataset(config)

    vn = datasets[DATABASE_VN]
    assert vn[0]["CreatedOn"] == end_at - timedelta(hours=12)
    assert vn[-1]["CreatedOn"] == end_at
    assert len({row["Amount"] for row in vn}) > 100
    assert {row["Status"] for row in vn} >= {0, 1, 2, 5}
    assert set(ground_truth["canonical"]["VN"]["by_status"]) >= {
        "FAILED",
        "PENDING",
        "SUCCESS",
        "UNKNOWN",
    }

    asia = datasets[DATABASE_ASIA_ID] + datasets[DATABASE_ASIA_PH]
    assert len({row["Channel"] for row in asia}) == 4
    assert datasets[DATABASE_ASIA_ID][0]["CreatedAt"] == end_at - timedelta(hours=12)
    assert datasets[DATABASE_ASIA_ID][-1]["CreatedAt"] == end_at
    assert datasets[DATABASE_ASIA_PH][0]["CreatedAt"] == end_at - timedelta(hours=12)
    assert datasets[DATABASE_ASIA_PH][-1]["CreatedAt"] == end_at
    india = datasets[DATABASE_INDIA]
    assert len({row["instrumentType"] for row in india}) == 4

    for region in ("VN", "ASIA", "INDIA"):
        statuses = ground_truth["canonical"][region]["by_status"]
        total = sum(statuses.values())
        healthy = statuses.get("SUCCESS", 0) + statuses.get("REFUNDED", 0)
        assert healthy / total > 0.80
        assert statuses.get("FAILED", 0) / total < 0.10
        assert statuses.get("PENDING", 0) / total < 0.10
        assert statuses.get("UNKNOWN", 0) / total < 0.20


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
    assert "referenceId" not in india
    assert isinstance(india["amount"], Decimal)


def test_dataset_contains_week_two_mapping_scenarios() -> None:
    _, ground_truth = generate_dataset(
        GeneratorConfig(seed=20260923, records_per_region=32)
    )
    required = {
        "exact-match",
        "amount-mismatch",
        "currency-mismatch",
        "missing-in-provider",
        "duplicate",
        "fuzzy-match",
        "not-reconcilable",
        "invalid-data",
    }
    for region in ("VN", "ASIA", "INDIA"):
        assert required <= set(ground_truth["canonical"][region]["scenarios"])
        assert sum(ground_truth["canonical"][region]["by_status"].values()) == 32


def test_duplicates_exist_only_in_provider_fixtures() -> None:
    config = GeneratorConfig(seed=20260923, records_per_region=64)
    datasets, _ = generate_dataset(config)

    for database, documents in datasets.items():
        mongo_ids = [document["_id"] for document in documents]
        assert len(mongo_ids) == len(set(mongo_ids)), database

    statements = generate_provider_statements(datasets, config)
    for filename, content in statements.items():
        report = CsvFixtureParser().parse(content.encode(), filename)
        assert report.valid
        identities = [
            transaction.provider_ref_id
            if transaction.region.value == "ASIA"
            and transaction.txn_type.value == "REFUND"
            else transaction.provider_txn_id
            for transaction in report.rows
        ]
        assert max(Counter(identities).values()) > 1


def test_region_specific_identity_cases_are_preserved() -> None:
    config = GeneratorConfig(seed=20260923, records_per_region=32)
    datasets, _ = generate_dataset(config)

    for database in (DATABASE_ASIA_ID, DATABASE_ASIA_PH):
        payment = datasets[database][0]
        refund = datasets[database][4]
        assert refund["Type"] == 2
        assert refund["ExternalTxnId"] == payment["ExternalTxnId"]
        assert refund["RefundFor"] == payment["_id"]
        assert refund["PaymentRefId"] != payment["PaymentRefId"]

    vn_payment = datasets[DATABASE_VN][0]
    vn_refund = datasets[DATABASE_VN][4]
    assert vn_payment["Status"] == 4
    assert vn_refund["PaymentMethod"] == "REFUND"
    assert vn_refund["OrderInfo"] == vn_payment["_id"]

    india_payment = datasets[DATABASE_INDIA][0]
    india_refund = datasets[DATABASE_INDIA][4]
    assert india_payment["status"] == "refunded"
    assert india_payment["amountRefunded"] == india_refund["amount"]
    assert india_refund["type"] == "REFUND"
    assert india_refund["status"] == "processed"
    assert india_refund["relatedPaymentId"] == india_payment["transactionId"]

    for document in datasets[DATABASE_INDIA]:
        assert document["transactionId"]
        assert "referenceId" not in document


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


def test_provider_statements_are_deterministic_and_parseable() -> None:
    config = GeneratorConfig(seed=20260923, records_per_region=32)
    datasets, _ = generate_dataset(config)

    first = generate_provider_statements(datasets, config)
    second = generate_provider_statements(datasets, config)

    assert first == second
    assert set(first) == {
        "onepay_20260923.csv",
        "espay_asia-id_20260923.csv",
        "2c2p_asia-ph_20260923.csv",
        "razorpay_20260923.csv",
    }
    for filename, content in first.items():
        report = CsvFixtureParser().parse(content.encode(), filename)
        assert report.valid, (filename, report.issues)
        assert report.rows, filename


def test_provider_statements_fit_their_business_date_period() -> None:
    config = GeneratorConfig(seed=20260923, records_per_region=32)
    datasets, _ = generate_dataset(config)
    statements = generate_provider_statements(datasets, config)
    timezones = {
        "onepay_20260923.csv": "Asia/Ho_Chi_Minh",
        "espay_asia-id_20260923.csv": "Asia/Jakarta",
        "2c2p_asia-ph_20260923.csv": "Asia/Manila",
        "razorpay_20260923.csv": "Asia/Kolkata",
    }

    for filename, content in statements.items():
        period = resolve_business_period(
            date(2026, 9, 23), timezones[filename], time(2),
        )
        rows = CsvFixtureParser().parse(content.encode(), filename).rows
        assert all(
            period.start_utc <= row.transaction_time < period.end_utc
            for row in rows
        ), filename


def test_large_dataset_stays_inside_each_business_period() -> None:
    config = GeneratorConfig(seed=20261001, records_per_region=200_000)

    for scope in scope_specs(config):
        count = scope_document_count(config, scope)
        period = resolve_business_period(
            date(2026, 10, 1), scope.business_timezone, time(2)
        )
        first = base_case(config, scope, 0).transaction_time
        last = base_case(config, scope, count - 1).transaction_time

        assert period.start_utc == first
        assert first <= last < period.end_utc


def test_provider_statements_do_not_depend_on_canonical_adapter_output() -> None:
    config = GeneratorConfig(seed=20260923, records_per_region=32)
    datasets, _ = generate_dataset(config)
    original = generate_provider_statements(datasets, config)

    datasets[DATABASE_VN][0]["Amount"] = "999999999"
    datasets[DATABASE_ASIA_ID][0]["ExternalTxnId"] = "adapter-side-change"
    datasets[DATABASE_INDIA][0]["transactionId"] = "adapter-side-change"

    assert generate_provider_statements(datasets, config) == original


def test_streaming_files_match_legacy_outputs(tmp_path) -> None:
    config = GeneratorConfig(seed=20260923, records_per_region=32)
    datasets, legacy_ground_truth = generate_dataset(config)
    legacy_statements = generate_provider_statements(datasets, config)

    ground_truth_path = tmp_path / "ground-truth.json"
    write_streaming_ground_truth(ground_truth_path, config)
    assert json.loads(ground_truth_path.read_text(encoding="utf-8")) == (
        legacy_ground_truth
    )

    for scope in scope_specs(config):
        statement_path = tmp_path / scope.filename
        row_count = write_provider_statement(
            statement_path, config, scope, len(datasets[scope.database])
        )
        assert statement_path.read_text(encoding="utf-8") == (
            legacy_statements[scope.filename]
        )
        assert row_count == len(statement_path.read_text().splitlines()) - 1


def test_generated_provider_statements_match_the_exact_oracle() -> None:
    config = GeneratorConfig(seed=20260923, records_per_region=32)
    datasets, ground_truth = generate_dataset(config)
    statements = generate_provider_statements(datasets, config)
    system_transactions = {
        "onepay_20260923.csv": [
            vn_to_canonical(document, VN_MAPPING)
            for document in datasets[DATABASE_VN]
        ],
        "espay_asia-id_20260923.csv": [
            asia_to_canonical(document, "asia-id", ASIA_MAPPING)
            for document in datasets[DATABASE_ASIA_ID]
        ],
        "2c2p_asia-ph_20260923.csv": [
            asia_to_canonical(document, "asia-ph", ASIA_MAPPING)
            for document in datasets[DATABASE_ASIA_PH]
        ],
        "razorpay_20260923.csv": [
            india_to_canonical(document) for document in datasets[DATABASE_INDIA]
        ],
    }

    for filename, content in statements.items():
        report = CsvFixtureParser().parse(content.encode(), filename)
        system_rows = [
            transaction
            for transaction in system_transactions[filename]
            if not transaction.is_deleted and not transaction.is_test_data
        ]
        results = MatchingEngine().match(filename, list(report.rows), system_rows)
        oracle = ground_truth["reconciliation"][filename]
        actual_summary = dict(
            sorted(Counter(result.result_type.value for result in results).items())
        )
        assert len(system_rows) == oracle["system_rows"]
        assert len(report.rows) == oracle["provider_rows"]
        assert actual_summary == oracle["expected_summary"]
        assert set(actual_summary) == {item.value for item in ReconResultType}

        actual_results = []
        for result in results:
            values = asdict(result)
            values.pop("run_id")
            values["result_type"] = result.result_type.value
            values["region"] = result.region.value
            values["txn_type"] = (
                result.txn_type.value if result.txn_type is not None else None
            )
            values["system_status"] = (
                result.system_status.value
                if result.system_status is not None else None
            )
            actual_results.append(values)
        expected_results = []
        for result in oracle["expected_results"]:
            values = dict(result)
            values.pop("case_id")
            expected_results.append(values)

        sort_key = lambda item: json.dumps(item, sort_keys=True)  # noqa: E731
        assert sorted(actual_results, key=sort_key) == sorted(
            expected_results, key=sort_key
        )


def test_generated_refunds_populate_financial_summary_panels() -> None:
    config = GeneratorConfig(seed=20260923, records_per_region=32)
    datasets, _ = generate_dataset(config)
    statements = generate_provider_statements(datasets, config)
    cases = (
        (
            "onepay_20260923.csv",
            [vn_to_canonical(row, VN_MAPPING) for row in datasets[DATABASE_VN]],
            "LINKED",
        ),
        (
            "razorpay_20260923.csv",
            [india_to_canonical(row) for row in datasets[DATABASE_INDIA]],
            "MATCHED",
        ),
    )

    for filename, system_rows, expected_cross_check in cases:
        provider_rows = CsvFixtureParser().parse(
            statements[filename].encode(), filename
        ).rows
        results = MatchingEngine().match(filename, list(provider_rows), system_rows)
        summary = build_financial_summary(results)

        assert summary.refunds_by_currency
        assert sum(item.count for item in summary.refunds_by_currency) > 0
        assert summary.refund_parent_metrics.refunded_parent_count > 0
        assert summary.refund_cross_checks
        assert {item.status for item in summary.refund_cross_checks} == {
            expected_cross_check
        }
