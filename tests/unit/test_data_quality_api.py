from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from reconciliation.adapters.data_quality import evaluate_rules, router
from reconciliation.application.ports import QueryUnavailable


RUN_ID = UUID("00000000-0000-0000-0000-000000000001")


def test_five_rules_point_to_affected_result_keys() -> None:
    groups = [
        {"row_count": 1, "affected_keys": [index] if index <= 5 else None,
         "result_type": result_type}
        for index, result_type in enumerate([
            "DUPLICATE",
            "INVALID_DATA",
            "CURRENCY_MISMATCH",
            "MISSING_IN_SYSTEM",
            "MISSING_IN_PROVIDER",
            "MATCHED",
        ], 1)
    ]

    findings = evaluate_rules(groups)

    assert len(findings) == 5
    assert {finding["rule_code"] for finding in findings} == {
        "DUPLICATE_TRANSACTION",
        "INVALID_DATA",
        "CURRENCY_MISMATCH",
        "MISSING_IN_SYSTEM",
        "MISSING_IN_PROVIDER",
    }
    assert all(finding["status"] == "FAILED" for finding in findings)
    assert [finding["affected_result_keys"] for finding in findings] == [
        [1], [2], [3], [4], [5],
    ]
    assert all(finding["evaluated_count"] == 6 for finding in findings)


def test_normal_results_pass_all_rules() -> None:
    findings = evaluate_rules([
        {"row_count": 100000, "affected_keys": None, "result_type": "MATCHED"},
        {"row_count": 5000, "affected_keys": None, "result_type": "FUZZY_MATCHED"},
    ])

    assert all(finding["status"] == "PASSED" for finding in findings)
    assert all(finding["affected_result_keys"] == [] for finding in findings)
    assert all(finding["evaluated_count"] == 105000 for finding in findings)


def test_empty_completed_run_has_five_passed_findings() -> None:
    findings = evaluate_rules([])
    assert len(findings) == 5
    assert all(finding["status"] == "PASSED" for finding in findings)
    assert all(finding["evaluated_count"] == finding["affected_count"] == 0 for finding in findings)


def test_group_counts_include_non_dq_types_and_keep_all_affected_keys() -> None:
    findings = evaluate_rules([
        {"result_type": "INVALID_DATA", "row_count": 3, "affected_keys": [2, 8, 11]},
        {"result_type": "MATCHED", "row_count": 100, "affected_keys": None},
        {"result_type": "NOT_RECONCILABLE", "row_count": 4, "affected_keys": None},
        {"result_type": "FUTURE_CLASSIFICATION", "row_count": 1, "affected_keys": None},
    ])
    assert all(finding["evaluated_count"] == 108 for finding in findings)
    invalid = next(finding for finding in findings if finding["rule_code"] == "INVALID_DATA")
    assert invalid["affected_count"] == 3
    assert invalid["affected_result_keys"] == [2, 8, 11]
    assert sum(finding["status"] == "FAILED" for finding in findings) == 1


class QueryStore:
    def __init__(self) -> None:
        self.filters = None

    def list(self, **filters):
        self.filters = filters
        return [{
            "business_date": date(2026, 10, 4),
            "run_id": RUN_ID,
            "region": "VN",
            "tenant_id": None,
            "provider": "ONEPAY",
            "rule_code": "INVALID_DATA",
            "rule_version": 1,
            "severity": "CRITICAL",
            "status": "FAILED",
            "evaluated_count": 10,
            "affected_count": 1,
            "affected_result_keys": [7],
            "evaluated_at": datetime(2026, 10, 4, 3, 1, tzinfo=UTC),
        }]


def client(store) -> TestClient:
    app = FastAPI()
    app.state.data_quality = store
    app.include_router(router)
    return TestClient(app)


def test_api_returns_findings_and_normalizes_filters() -> None:
    store = QueryStore()

    response = client(store).get("/internal/data-quality", params={
        "region": "VN",
        "provider": "onepay",
        "status": "FAILED",
        "rule_code": "invalid_data",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["affected_result_keys"] == [7]
    assert body["meta"]["data_as_of"]
    assert store.filters["provider"] == "ONEPAY"
    assert store.filters["rule_code"] == "INVALID_DATA"


def test_api_hides_database_error() -> None:
    class UnavailableStore:
        def list(self, **filters):
            raise QueryUnavailable("private database details")

    response = client(UnavailableStore()).get("/internal/data-quality")

    assert response.status_code == 503
    assert response.json() == {"detail": "QUERY_UNAVAILABLE"}
