from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID

from monitoring.domain import AlertStatus, FreshnessStatus, HealthStatus, Severity
from shared_kernel import Region, ensure_utc


@dataclass(frozen=True, slots=True)
class ProviderHealthQuery:
    region: Region | None = None
    tenant_id: str | None = None
    provider: str | None = None
    status: HealthStatus | None = None
    limit: int = 100
    offset: int = 0
    sort: str = "scope"

    def __post_init__(self) -> None:
        _validate_scope_filters(self.tenant_id, self.provider)
        if not 1 <= self.limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if self.offset < 0:
            raise ValueError("cursor offset cannot be negative")
        if self.sort != "scope":
            raise ValueError("sort must be one of: scope")


@dataclass(frozen=True, slots=True)
class ProviderHealthItem:
    region: Region
    tenant_id: str | None
    provider: str
    status: HealthStatus
    reasons: Sequence[Mapping[str, object]]
    evaluations: Sequence[Mapping[str, object]]
    evaluated_at: datetime
    data_as_of: datetime | None
    open_alert_ids: Sequence[str]


@dataclass(frozen=True, slots=True)
class ProviderHealthPage:
    items: Sequence[ProviderHealthItem]
    data_as_of: datetime | None
    next_offset: int | None = None


@dataclass(frozen=True, slots=True)
class AlertQuery:
    status: AlertStatus | None = None
    severity: Severity | None = None
    rule_id: str | None = None
    region: Region | None = None
    tenant_id: str | None = None
    provider: str | None = None
    limit: int = 100
    cursor_opened_at: datetime | None = None
    cursor_alert_id: UUID | None = None
    sort: str = "-opened_at"

    def __post_init__(self) -> None:
        _validate_scope_filters(self.tenant_id, self.provider)
        if self.rule_id is not None and not self.rule_id.strip():
            raise ValueError("rule_id cannot be empty")
        if not 1 <= self.limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if (self.cursor_opened_at is None) != (self.cursor_alert_id is None):
            raise ValueError("cursor sort keys must be supplied together")
        if self.sort != "-opened_at":
            raise ValueError("sort must be one of: -opened_at")
        if self.cursor_opened_at is not None:
            object.__setattr__(
                self, "cursor_opened_at", ensure_utc(self.cursor_opened_at)
            )


@dataclass(frozen=True, slots=True)
class AlertItem:
    alert_id: str
    region: Region
    tenant_id: str | None
    provider: str
    rule_id: str
    rule_version: int
    status: AlertStatus
    severity: Severity
    health_impact: str
    reason_code: str
    reason_message: str
    observed_value: object | None
    comparison: str
    threshold: object
    sample_size: int | None
    minimum_sample: int | None
    window_size_minutes: int | None
    evaluated_window_count: int
    reasons: Sequence[Mapping[str, object]]
    occurrence_count: int
    cooldown_seconds: int
    runbook_ref: str
    data_as_of: datetime | None
    opened_at: datetime
    last_seen_at: datetime
    last_evaluated_at: datetime
    last_notified_at: datetime | None
    resolved_at: datetime | None
    txn_ids: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class AlertPage:
    items: Sequence[AlertItem]
    data_as_of: datetime | None
    next_key: tuple[datetime, UUID] | None


@dataclass(frozen=True, slots=True)
class FreshnessQuery:
    region: Region | None = None
    tenant_id: str | None = None
    provider: str | None = None
    status: FreshnessStatus | None = None
    limit: int = 100
    offset: int = 0
    sort: str = "scope"

    def __post_init__(self) -> None:
        _validate_scope_filters(self.tenant_id, self.provider)
        if not 1 <= self.limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if self.offset < 0:
            raise ValueError("cursor offset cannot be negative")
        if self.sort != "scope":
            raise ValueError("sort must be one of: scope")


@dataclass(frozen=True, slots=True)
class FreshnessItem:
    source_name: str
    region: Region
    tenant_id: str | None
    provider: str
    last_run_at: datetime
    last_success_at: datetime | None
    data_as_of: datetime | None
    evaluated_at: datetime
    lag_seconds: int | None
    sla_seconds: int
    sla_status: FreshnessStatus
    consecutive_failures: int
    last_error_code: str | None


@dataclass(frozen=True, slots=True)
class FreshnessPage:
    items: Sequence[FreshnessItem]
    data_as_of: datetime | None
    next_offset: int | None = None


def _validate_scope_filters(tenant_id: str | None, provider: str | None) -> None:
    for name, value in (("tenant_id", tenant_id), ("provider", provider)):
        if value is not None and (not value.strip() or len(value) > 100):
            raise ValueError(f"{name} must contain 1 to 100 characters")


class AlertCursorCodec:
    def __init__(self, secret: str) -> None:
        if len(secret) < 16:
            raise ValueError("cursor secret must contain at least 16 characters")
        self.secret = secret.encode()

    def encode(self, key: tuple[datetime, UUID]) -> str:
        payload = json.dumps(
            {
                "v": 1,
                "opened_at": ensure_utc(key[0]).isoformat(),
                "alert_id": str(key[1]),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self.secret, encoded, hashlib.sha256).digest()
        return encoded.decode() + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()

    def decode(self, cursor: str) -> tuple[datetime, UUID]:
        try:
            encoded, supplied = cursor.split(".", 1)
            encoded_bytes = encoded.encode()
            expected = hmac.new(self.secret, encoded_bytes, hashlib.sha256).digest()
            signature = base64.urlsafe_b64decode(_pad(supplied))
            if not hmac.compare_digest(signature, expected):
                raise ValueError("cursor signature is invalid")
            document = json.loads(base64.urlsafe_b64decode(_pad(encoded)))
            if document.get("v") != 1 or set(document) != {"v", "opened_at", "alert_id"}:
                raise ValueError("cursor version or payload is invalid")
            opened_at = ensure_utc(datetime.fromisoformat(document["opened_at"]))
            return opened_at, UUID(document["alert_id"])
        except Exception as error:
            raise ValueError("cursor is invalid") from error


def _pad(value: str) -> bytes:
    return (value + "=" * (-len(value) % 4)).encode()


class OffsetCursorCodec:
    """Signed opaque cursor bound to one endpoint and its stable sort."""

    def __init__(self, secret: str) -> None:
        if len(secret) < 16:
            raise ValueError("cursor secret must contain at least 16 characters")
        self.secret = secret.encode()

    def encode(self, *, endpoint: str, offset: int) -> str:
        payload = json.dumps(
            {"v": 1, "endpoint": endpoint, "offset": offset},
            separators=(",", ":"), sort_keys=True,
        ).encode()
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self.secret, encoded, hashlib.sha256).digest()
        return encoded.decode() + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()

    def decode(self, cursor: str, *, endpoint: str) -> int:
        try:
            encoded, supplied = cursor.split(".", 1)
            encoded_bytes = encoded.encode()
            expected = hmac.new(self.secret, encoded_bytes, hashlib.sha256).digest()
            signature = base64.urlsafe_b64decode(_pad(supplied))
            if not hmac.compare_digest(signature, expected):
                raise ValueError("cursor signature is invalid")
            document = json.loads(base64.urlsafe_b64decode(_pad(encoded)))
            if set(document) != {"v", "endpoint", "offset"}:
                raise ValueError("cursor payload is invalid")
            if document["v"] != 1 or document["endpoint"] != endpoint:
                raise ValueError("cursor endpoint or version is invalid")
            offset = int(document["offset"])
            if offset < 0:
                raise ValueError("cursor offset is invalid")
            return offset
        except Exception as error:
            raise ValueError("cursor is invalid") from error
