from datetime import UTC, datetime
from threading import Lock
from typing import Literal
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

class AlertmanagerAlert(BaseModel):
    status: Literal["firing", "resolved"]
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: datetime
    endsAt: datetime | None = None
    generatorURL: str = ""
    fingerprint: str = Field(min_length=1)

class AlertmanagerWebhook(BaseModel):
    status: Literal["firing", "resolved"]
    alerts: list[AlertmanagerAlert]

class AlertView(BaseModel):
    fingerprint: str
    status: Literal["firing", "resolved"]
    name: str
    severity: str
    labels: dict[str, str]
    annotations: dict[str, str]
    starts_at: datetime
    ends_at: datetime | None
    generator_url: str
    received_at: datetime

class InMemoryAlertStore:
    """Keep the latest state of each Alertmanager fingerprint."""

    def __init__(self, max_alerts: int = 1_000) -> None:
        self.max_alerts = max_alerts
        self._alerts: dict[str, AlertView] = {}
        self._lock = Lock()

    def ingest(self, payload: AlertmanagerWebhook) -> int:
        received_at = datetime.now(UTC)
        with self._lock:
            for alert in payload.alerts:
                self._alerts[alert.fingerprint] = AlertView(
                    fingerprint=alert.fingerprint,
                    status=alert.status,
                    name=alert.labels.get("alertname", "UNKNOWN"),
                    severity=alert.labels.get("severity", "unknown"),
                    labels=alert.labels,
                    annotations=alert.annotations,
                    starts_at=alert.startsAt,
                    ends_at=alert.endsAt,
                    generator_url=alert.generatorURL,
                    received_at=received_at,
                )
            if len(self._alerts) > self.max_alerts:
                oldest = sorted(
                    self._alerts.values(), key=lambda item: item.received_at
                )[: len(self._alerts) - self.max_alerts]
                for item in oldest:
                    self._alerts.pop(item.fingerprint, None)
        return len(payload.alerts)

    def list(self, status: Literal["firing", "resolved"] | None = None) -> list[AlertView]:
        with self._lock:
            alerts = list(self._alerts.values())
        if status is not None:
            alerts = [alert for alert in alerts if alert.status == status]
        return sorted(alerts, key=lambda item: item.received_at, reverse=True)

router = APIRouter(tags=["alerts"])

@router.post("/internal/alerts/webhook")
def receive_alertmanager_webhook(
    request: Request, payload: AlertmanagerWebhook
) -> dict[str, int]:
    store: InMemoryAlertStore = request.app.state.alert_store
    return {"accepted": store.ingest(payload)}

@router.get("/alerts")
def list_alerts(
    request: Request,
    status: Literal["firing", "resolved"] | None = Query(default=None),
) -> dict[str, object]:
    store: InMemoryAlertStore = request.app.state.alert_store
    return {
        "items": store.list(status),
        "meta": {"data_as_of": datetime.now(UTC)},
    }