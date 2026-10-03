from datetime import date, time
from io import BytesIO
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile
from starlette.requests import Request

from reconciliation.adapters.http import create_reconciliation_run, router
from shared_kernel import Region


def test_create_reconciliation_run_forwards_uploaded_statement() -> None:
    received: dict[str, object] = {}
    accepted_run_id = UUID("00000000-0000-0000-0000-000000000001")

    def submit_reconciliation(**values):
        received.update(values)
        return accepted_run_id

    app = FastAPI()
    app.state.submit_reconciliation = submit_reconciliation
    request = Request({"type": "http", "app": app})
    statement = UploadFile(filename="onepay.csv", file=BytesIO(b"csv-content"))

    response = create_reconciliation_run(
        request=request,
        statement=statement,
        business_date=date(2026, 9, 22),
        region=Region.VN,
        provider="ONEPAY",
        business_timezone="Asia/Ho_Chi_Minh",
        cutoff_time=time(2),
        tenant_id=None,
    )

    body = response.model_dump(mode="json")
    assert body["run_id"] == str(accepted_run_id)
    assert body["status"] == "PENDING"
    assert received["statement_content"] == b"csv-content"
    assert received["business_timezone"] == "Asia/Ho_Chi_Minh"
    assert received["cutoff_time"] == time(2)
    assert str(received["statement"]) == "onepay.csv"
    assert isinstance(received["run_id"], UUID)


def test_http_endpoint_returns_accepted_immediately() -> None:
    run_id = UUID("00000000-0000-0000-0000-000000000001")
    app = FastAPI()
    app.include_router(router)
    app.state.submit_reconciliation = lambda **_: run_id

    response = TestClient(app).post(
        "/reconciliation-runs",
        data={
            "business_date": "2026-09-22",
            "region": "VN",
            "provider": "ONEPAY",
            "business_timezone": "Asia/Ho_Chi_Minh",
            "cutoff_time": "02:00:00",
        },
        files={"statement": ("onepay.csv", b"csv-content", "text/csv")},
    )

    assert response.status_code == 202
    assert response.json()["run_id"] == str(run_id)
    assert response.json()["status"] == "PENDING"
