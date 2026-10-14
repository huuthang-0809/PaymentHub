from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aio_pika
from aio_pika import DeliveryMode, ExchangeType, Message
from aio_pika.abc import (
    AbstractChannel,
    AbstractIncomingMessage,
    AbstractRobustConnection,
)

from canonical.domain import CanonicalTransaction
from monitoring.domain import CanonicalEvent
from shared_kernel import AmountStatus, CanonicalStatus, Region, TxnType
from shared_kernel.enums import Instrument


MAIN_EXCHANGE = "payment.events"
MAIN_QUEUE = "payment.monitoring.ingest"
MAIN_ROUTING_KEY = "monitoring.canonical"

RETRY_EXCHANGE = "payment.monitoring.retry"
RETRY_5S_QUEUE = "payment.monitoring.retry.5s"
RETRY_30S_QUEUE = "payment.monitoring.retry.30s"
RETRY_5M_QUEUE = "payment.monitoring.retry.5m"

DLX_EXCHANGE = "payment.monitoring.dlx"
DLQ_QUEUE = "payment.monitoring.dlq"
DLQ_ROUTING_KEY = "monitoring.dead"

RETRY_STEPS = (("retry.5s", 5), ("retry.30s", 30), ("retry.5m", 300))
_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "card_number",
        "cvv",
        "password",
        "secret",
        "token",
    }
)


class PermanentMessageError(ValueError):
    """A malformed message that cannot succeed when retried unchanged."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ConsumerResult:
    disposition: str
    retry_count: int = 0


@dataclass(frozen=True, slots=True)
class SourceHeartbeat:
    source_name: str
    region: Region
    tenant_scope: str
    provider: str
    sent_at: datetime
    received_at: datetime


def mask_sensitive(value: Any) -> Any:
    """Copy a JSON value while masking secrets recursively."""
    if isinstance(value, Mapping):
        return {
            str(key): (
                "***MASKED***"
                if str(key).lower() in _SENSITIVE_KEYS
                else mask_sensitive(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [mask_sensitive(item) for item in value]
    return value


def _required(payload: Mapping[str, Any], name: str) -> Any:
    if name not in payload:
        raise PermanentMessageError("MISSING_FIELD", f"missing required field: {name}")
    return payload[name]


def _boolean(payload: Mapping[str, Any], name: str, *, default: bool | None = None) -> bool:
    value = payload.get(name, default)
    if not isinstance(value, bool):
        raise PermanentMessageError("INVALID_FIELD", f"{name} must be boolean")
    return value


def _timestamp(value: Any, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PermanentMessageError("INVALID_TIMESTAMP", f"{name} must be ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PermanentMessageError(
            "INVALID_TIMESTAMP", f"{name} must be ISO-8601"
        ) from error
    if parsed.tzinfo is None:
        raise PermanentMessageError(
            "INVALID_TIMESTAMP", f"{name} must include timezone"
        )
    return parsed


def decode_canonical_event(body: bytes) -> CanonicalEvent | SourceHeartbeat:
    """Decode the version-1 canonical-event JSON wire contract."""
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PermanentMessageError("INVALID_JSON", "body must be valid JSON") from error
    if not isinstance(payload, dict):
        raise PermanentMessageError("INVALID_SHAPE", "body must be a JSON object")
    if payload.get("schema_version", 1) != 1:
        raise PermanentMessageError("UNSUPPORTED_SCHEMA", "schema_version must be 1")
    if payload.get("message_type") == "heartbeat":
        sent_at = _timestamp(_required(payload, "sent_at"), "sent_at")
        if sent_at is None:
            raise PermanentMessageError("INVALID_TIMESTAMP", "sent_at is required")
        try:
            return SourceHeartbeat(
                source_name=str(payload.get("source_name", "rabbitmq:canonical-events")),
                region=Region(_required(payload, "region")),
                tenant_scope=str(payload.get("tenant_id") or ""),
                provider=str(_required(payload, "provider")),
                sent_at=sent_at,
                received_at=datetime.now(UTC),
            )
        except (TypeError, ValueError) as error:
            raise PermanentMessageError("MAPPING_ERROR", str(error)) from error
    try:
        instrument = payload.get("instrument")
        raw = payload.get("raw", {})
        if not isinstance(raw, dict):
            raise PermanentMessageError("INVALID_FIELD", "raw must be an object")
        transaction = CanonicalTransaction(
            region=Region(_required(payload, "region")),
            tenant_id=payload.get("tenant_id"),
            txn_id=str(_required(payload, "txn_id")),
            txn_type=TxnType(_required(payload, "txn_type")),
            instrument=Instrument(instrument) if instrument else None,
            channel_raw=payload.get("channel_raw"),
            provider=str(_required(payload, "provider")),
            status=CanonicalStatus(_required(payload, "status")),
            amount_status=AmountStatus(_required(payload, "amount_status")),
            amount_minor=payload.get("amount_minor"),
            currency=payload.get("currency"),
            created_at=_timestamp(_required(payload, "created_at"), "created_at"),
            updated_at=_timestamp(payload.get("updated_at"), "updated_at"),
            is_terminal=_boolean(payload, "is_terminal"),
            is_money_movement=_boolean(payload, "is_money_movement"),
            is_reconcilable=_boolean(payload, "is_reconcilable"),
            is_deleted=_boolean(payload, "is_deleted", default=False),
            is_test_data=_boolean(payload, "is_test_data", default=False),
            provider_txn_id=payload.get("provider_txn_id"),
            provider_ref_id=payload.get("provider_ref_id"),
            merchant_ref=payload.get("merchant_ref"),
            refund_of=payload.get("refund_of"),
            raw=raw,
        )
        return CanonicalEvent(
            event_id=str(_required(payload, "event_id")),
            transaction=transaction,
            parse_error=_boolean(payload, "parse_error", default=False),
            # Set at ingress. Batch/replay constructors leave this absent, so
            # historical replay is not incorrectly classified by wall clock.
            received_at=datetime.now(UTC),
        )
    except PermanentMessageError:
        raise
    except (TypeError, ValueError) as error:
        raise PermanentMessageError("MAPPING_ERROR", str(error)) from error


class RabbitMQMessageHandler:
    """Apply ACK-after-commit, retry and DLQ policy to one message."""

    def __init__(
        self,
        channel: AbstractChannel,
        process: Callable[[CanonicalEvent | SourceHeartbeat], Awaitable[None] | None],
        *,
        transient_errors: tuple[type[BaseException], ...] = (OSError, TimeoutError),
    ) -> None:
        self.channel = channel
        self.process = process
        self.transient_errors = transient_errors

    async def __call__(self, message: AbstractIncomingMessage) -> ConsumerResult:
        try:
            event = decode_canonical_event(message.body)
            result = self.process(event)
            if inspect.isawaitable(result):
                await result
        except PermanentMessageError as error:
            await self._dead_letter(message, error.code, str(error))
            await message.ack()
            return ConsumerResult("DEAD_LETTER")
        except self.transient_errors as error:
            retry_count = int((message.headers or {}).get("x-retry-count", 0))
            if retry_count < len(RETRY_STEPS):
                await self._retry(message, retry_count)
                await message.ack()
                return ConsumerResult("RETRY", retry_count + 1)
            await self._dead_letter(message, "RETRY_EXHAUSTED", str(error))
            await message.ack()
            return ConsumerResult("DEAD_LETTER", retry_count)
        except Exception as error:
            await self._dead_letter(message, "PROCESSING_ERROR", str(error))
            await message.ack()
            return ConsumerResult("DEAD_LETTER")

        # process() returns only after the PostgreSQL transaction committed.
        await message.ack()
        return ConsumerResult("ACK")

    async def _retry(self, message: AbstractIncomingMessage, index: int) -> None:
        routing_key, _delay_seconds = RETRY_STEPS[index]
        headers = dict(message.headers or {})
        headers["x-retry-count"] = index + 1
        exchange = await self.channel.get_exchange(RETRY_EXCHANGE)
        await exchange.publish(
            Message(
                message.body,
                headers=headers,
                content_type="application/json",
                delivery_mode=DeliveryMode.PERSISTENT,
                correlation_id=message.correlation_id,
                message_id=message.message_id,
            ),
            routing_key=routing_key,
        )

    async def _dead_letter(
        self, message: AbstractIncomingMessage, code: str, detail: str
    ) -> None:
        try:
            raw: Any = json.loads(message.body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raw = {"unparseable_body": message.body.decode("utf-8", errors="replace")}
        envelope = {
            "error_code": code,
            "error_detail": detail[:500],
            "raw_reference": message.message_id or message.correlation_id,
            "retry_count": int((message.headers or {}).get("x-retry-count", 0)),
            "payload": mask_sensitive(raw),
        }
        exchange = await self.channel.get_exchange(DLX_EXCHANGE)
        await exchange.publish(
            Message(
                json.dumps(envelope, separators=(",", ":")).encode(),
                content_type="application/json",
                delivery_mode=DeliveryMode.PERSISTENT,
                correlation_id=message.correlation_id,
                message_id=message.message_id,
            ),
            routing_key=DLQ_ROUTING_KEY,
        )


async def reprocess_dlq_message(
    channel: AbstractChannel, message: AbstractIncomingMessage
) -> None:
    """Republish one corrected DLQ envelope; DB idempotency makes replay safe."""
    try:
        envelope = json.loads(message.body)
        payload = envelope["payload"]
        decode_canonical_event(json.dumps(payload).encode())
    except (KeyError, TypeError, PermanentMessageError, json.JSONDecodeError) as error:
        raise PermanentMessageError("INVALID_DLQ_ENVELOPE", str(error)) from error
    exchange = await channel.get_exchange(MAIN_EXCHANGE)
    await exchange.publish(
        Message(
            json.dumps(payload, separators=(",", ":")).encode(),
            headers={"x-dlq-reprocessed": True},
            content_type="application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
            correlation_id=message.correlation_id,
            message_id=message.message_id,
        ),
        routing_key=MAIN_ROUTING_KEY,
    )
    await message.ack()


async def connect(url: str) -> AbstractRobustConnection:
    return await aio_pika.connect_robust(url)


async def declare_topology(
    channel: AbstractChannel,
) -> None:
    main_exchange = await channel.declare_exchange(
        MAIN_EXCHANGE,
        ExchangeType.DIRECT,
        durable=True,
    )

    main_queue = await channel.declare_queue(
        MAIN_QUEUE,
        durable=True,
        arguments={
            "x-queue-type": "quorum",
        },
    )

    await main_queue.bind(
        main_exchange,
        routing_key=MAIN_ROUTING_KEY,
    )

    retry_exchange = await channel.declare_exchange(
        RETRY_EXCHANGE,
        ExchangeType.DIRECT,
        durable=True,
    )

    await _declare_retry_queue(
        channel,
        retry_exchange,
        RETRY_5S_QUEUE,
        routing_key="retry.5s",
        ttl_ms=5_000,
    )
    await _declare_retry_queue(
        channel,
        retry_exchange,
        RETRY_30S_QUEUE,
        routing_key="retry.30s",
        ttl_ms=30_000,
    )
    await _declare_retry_queue(
        channel,
        retry_exchange,
        RETRY_5M_QUEUE,
        routing_key="retry.5m",
        ttl_ms=300_000,
    )

    dlx = await channel.declare_exchange(
        DLX_EXCHANGE,
        ExchangeType.DIRECT,
        durable=True,
    )

    dlq = await channel.declare_queue(
        DLQ_QUEUE,
        durable=True,
        arguments={
            "x-queue-type": "quorum",
        },
    )

    await dlq.bind(
        dlx,
        routing_key=DLQ_ROUTING_KEY,
    )


async def _declare_retry_queue(
    channel: AbstractChannel,
    retry_exchange,
    queue_name: str,
    *,
    routing_key: str,
    ttl_ms: int,
) -> None:
    queue = await channel.declare_queue(
        queue_name,
        durable=True,
        arguments={
            "x-message-ttl": ttl_ms,
            "x-dead-letter-exchange": MAIN_EXCHANGE,
            "x-dead-letter-routing-key": MAIN_ROUTING_KEY,
        },
    )

    await queue.bind(
        retry_exchange,
        routing_key=routing_key,
    )
