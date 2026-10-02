from fastapi import FastAPI
from fastapi.testclient import TestClient
from reconciliation.adapters.alerts import InMemoryAlertStore, router

def client() -> TestClient:
    app = FastAPI()
    app.state.alert_store = InMemoryAlertStore()
    app.include_router(router)
    return TestClient(app)

def payload(status: str) -> dict[str, object]:
    return {
        "status": status,
        "alerts": [{
            "status": status,
            "labels": {
                "alertname": "ProviderStatementMissing",
                "severity": "critical",
                "region": "VN",
                "provider": "ONEPAY",
            },
            "annotations": {"summary": "Provider statement is missing"},
            "startsAt": "2026-10-01T03:01:00Z",
            "endsAt": "2026-10-01T03:05:00Z" if status == "resolved" else None,
            "generatorURL": "http://prometheus:9090/graph",
            "fingerprint": "alert-1",
        }],
    }

def test_normal_state_has_no_alerts() -> None:
    response = client().get("/alerts")

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["meta"]["data_as_of"]

def test_webhook_is_visible_to_frontend_and_updates_resolved_state() -> None:
    api = client()

    response = api.post("/internal/alerts/webhook", json=payload("firing"))
    assert response.json() == {"accepted": 1}
    firing = api.get("/alerts", params={"status": "firing"}).json()["items"]
    assert len(firing) == 1
    assert firing[0]["name"] == "ProviderStatementMissing"
    assert firing[0]["labels"]["provider"] == "ONEPAY"

    api.post("/internal/alerts/webhook", json=payload("resolved"))

    assert api.get("/alerts", params={"status": "firing"}).json()["items"] == []
    resolved = api.get("/alerts", params={"status": "resolved"}).json()["items"]
    assert len(resolved) == 1
    assert resolved[0]["status"] == "resolved"

def test_invalid_webhook_is_rejected() -> None:
    response = client().post("/internal/alerts/webhook", json={"status": "firing"})

    assert response.status_code == 422