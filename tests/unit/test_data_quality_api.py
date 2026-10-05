from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from reconciliation.adapters.data_quality import evaluate_rules, router
from reconciliation.application.ports import QueryUnavailable


RUN_ID = UUID("00000000-0000-0000-0000-000000000001")


def test_five_rules_point_to_affected_result_keys() -> None:
    results = [
        {"result_key": index, "result_type": result_type}
        for index, result_type in enumerate([
            "DUPLICATE",
            "INVALID_DATA",
            "CURRENCY_MISMATCH",
            "MISSING_IN_SYSTEM",
            "MISSING_IN_PROVIDER",
            "MATCHED",
        ], 1)
    ]

    findings = evaluate_rules(results)

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
        {"result_key": 1, "result_type": "MATCHED"},
        {"result_key": 2, "result_type": "FUZZY_MATCHED"},
    ])

    assert all(finding["status"] == "PASSED" for finding in findings)
    assert all(finding["affected_result_keys"] == [] for finding in findings)


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
