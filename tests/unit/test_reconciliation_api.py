from datetime import date
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI
from fastapi import HTTPException
import pytest
from starlette.datastructures import UploadFile
from starlette.requests import Request

from reconciliation.adapters.http import create_reconciliation_run
from reconciliation.application import RunInProgress
from reconciliation.application.summary import build_financial_summary
from reconciliation.domain import ReconResult, ReconResultType
from shared_kernel import Region


def test_create_reconciliation_run_forwards_uploaded_statement() -> None:
    received: dict[str, object] = {}
    financial = build_financial_summary([ReconResult(
        run_id="run", result_type=ReconResultType.MISSING_IN_PROVIDER,
        region=Region.VN, tenant_id=None, provider="ONEPAY",
        system_txn_id="missing", system_amount_minor=50, system_currency="VND",
    )])

    def run_reconciliation(**values):
        received.update(values)
        return SimpleNamespace(
            run_id=UUID("00000000-0000-0000-0000-000000000001"),
            job_run_id=UUID("00000000-0000-0000-0000-000000000002"),
            system_rows=3,
            provider_rows=2,
            summary={"MATCHED": 2, "MISSING_IN_PROVIDER": 1},
            financial_summary=financial,
        )

    app = FastAPI()
    app.state.run_reconciliation = run_reconciliation
    request = Request({"type": "http", "app": app})
    statement = UploadFile(filename="onepay.csv", file=BytesIO(b"csv-content"))

    response = create_reconciliation_run(
        request=request,
        statement=statement,
        business_date=date(2026, 9, 22),
        region=Region.VN,
        provider="ONEPAY",
        tenant_id=None,
    )

    assert response.summary == {
        "MATCHED": 2,
        "MISSING_IN_PROVIDER": 1,
    }
    body = response.model_dump(mode="json")
    breakdown = body["financial_summary"]["breakdowns"][0]
    assert breakdown["region"] == "VN"
    assert breakdown["result"] == "MISSING_IN_PROVIDER"
    assert breakdown["system_count"] == 1
    assert breakdown["provider_count"] == 0
    assert body["financial_summary"]["refunds_by_currency"] == []
    assert received["statement_content"] == b"csv-content"
    assert str(received["statement"]) == "onepay.csv"
    body = response.model_dump(mode="json")
    assert body["financial_summary"]["totals_by_currency"][0]["delta_amount_minor"] == 50
    assert body["financial_summary"]["refund_parent_metrics"]["refunded_transaction_rate"] is None


def test_concurrent_same_input_returns_conflict() -> None:
    app = FastAPI()

    def in_progress(**values):
        raise RunInProgress("Identical reconciliation input is running")

    app.state.run_reconciliation = in_progress
    request = Request({"type": "http", "app": app})
    statement = UploadFile(filename="onepay.csv", file=BytesIO(b"csv-content"))

    with pytest.raises(HTTPException) as error:
        create_reconciliation_run(
            request=request, statement=statement,
            business_date=date(2026, 9, 22), region=Region.VN,
            provider="ONEPAY", tenant_id=None,
        )
    assert error.value.status_code == 409
