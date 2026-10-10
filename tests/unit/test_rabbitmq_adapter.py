import asyncio
import json

import pytest

from monitoring.adapters.rabbitmq import (
    DLX_EXCHANGE,
    MAIN_EXCHANGE,
    RETRY_EXCHANGE,
    PermanentMessageError,
    RabbitMQMessageHandler,
    decode_canonical_event,
    mask_sensitive,
    reprocess_dlq_message,
)


def _payload(**changes):
    value = {
        "schema_version": 1,
        "event_id": "rabbit-event-1",
        "region": "VN",
        "tenant_id": None,
        "txn_id": "txn-rabbit-1",
        "txn_type": "PAYMENT",
        "instrument": "CARD",
        "channel_raw": "card",
        "provider": "ONEPAY",
        "status": "SUCCESS",
        "amount_status": "OK",
        "amount_minor": 12500,
        "currency": "VND",
        "created_at": "2026-10-06T01:00:00Z",
        "updated_at": "2026-10-06T01:01:00Z",
        "is_terminal": True,
        "is_money_movement": True,
        "is_reconcilable": True,
        "raw": {"authorization": "Bearer secret", "safe": "visible"},
    }
    value.update(changes)
    return value


class _Exchange:
    def __init__(self, events, name):
        self.events = events
        self.name = name
        self.published = []

    async def publish(self, message, routing_key):
        self.events.append(f"publish:{self.name}:{routing_key}")
        self.published.append((message, routing_key))


class _Channel:
    def __init__(self, events):
        self.exchanges = {
            RETRY_EXCHANGE: _Exchange(events, RETRY_EXCHANGE),
            DLX_EXCHANGE: _Exchange(events, DLX_EXCHANGE),
            MAIN_EXCHANGE: _Exchange(events, MAIN_EXCHANGE),
        }

    async def get_exchange(self, name):
        return self.exchanges[name]


class _Incoming:
    def __init__(self, payload, events, *, headers=None):
        self.body = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        )
        self.headers = headers or {}
        self.message_id = "message-1"
        self.correlation_id = "correlation-1"
        self.events = events
        self.acked = False
        self.nacked = False

    async def ack(self):
        self.events.append("ack")
        self.acked = True

    async def nack(self, *, requeue):
        self.events.append(f"nack:{requeue}")
        self.nacked = True


def test_decode_canonical_event_and_reject_unsafe_boolean_coercion():
    event = decode_canonical_event(json.dumps(_payload()).encode())

    assert event.event_id == "rabbit-event-1"
    assert event.transaction.amount_minor == 12500
    with pytest.raises(PermanentMessageError, match="must be boolean"):
        decode_canonical_event(
            json.dumps(_payload(is_terminal="false")).encode()
        )


def test_ack_happens_only_after_database_handler_returns():
    events = []
    channel = _Channel(events)
    message = _Incoming(_payload(), events)

    async def process(_event):
        events.append("database-commit")

    result = asyncio.run(RabbitMQMessageHandler(channel, process)(message))

    assert result.disposition == "ACK"
    assert events == ["database-commit", "ack"]


def test_transient_error_publishes_to_retry_before_ack():
    events = []
    channel = _Channel(events)
    message = _Incoming(_payload(), events)

    def process(_event):
        raise TimeoutError("postgres temporarily unavailable")

    result = asyncio.run(RabbitMQMessageHandler(channel, process)(message))

    assert result.retry_count == 1
    assert events == [f"publish:{RETRY_EXCHANGE}:retry.5s", "ack"]
    published = channel.exchanges[RETRY_EXCHANGE].published[0][0]
    assert published.headers["x-retry-count"] == 1


def test_retry_exhaustion_goes_to_dlq_and_masks_raw_data():
    events = []
    channel = _Channel(events)
    message = _Incoming(_payload(), events, headers={"x-retry-count": 3})

    def process(_event):
        raise TimeoutError("still unavailable")

    result = asyncio.run(RabbitMQMessageHandler(channel, process)(message))

    assert result.disposition == "DEAD_LETTER"
    assert events[-1] == "ack"
    published = channel.exchanges[DLX_EXCHANGE].published[0][0]
    envelope = json.loads(published.body)
    assert envelope["error_code"] == "RETRY_EXHAUSTED"
    assert envelope["raw_reference"] == "message-1"
    assert envelope["payload"]["raw"]["authorization"] == "***MASKED***"


def test_mapping_error_goes_to_dlq_without_calling_processor():
    events = []
    channel = _Channel(events)
    message = _Incoming({"event_id": "broken", "password": "secret"}, events)
    called = False

    def process(_event):
        nonlocal called
        called = True

    result = asyncio.run(RabbitMQMessageHandler(channel, process)(message))

    assert result.disposition == "DEAD_LETTER"
    assert called is False
    envelope = json.loads(channel.exchanges[DLX_EXCHANGE].published[0][0].body)
    assert envelope["error_code"] == "MISSING_FIELD"
    assert envelope["payload"]["password"] == "***MASKED***"


def test_dlq_reprocess_publishes_main_before_ack():
    events = []
    channel = _Channel(events)
    message = _Incoming({"error_code": "FIXED", "payload": _payload()}, events)

    asyncio.run(reprocess_dlq_message(channel, message))

    assert events == [f"publish:{MAIN_EXCHANGE}:monitoring.canonical", "ack"]
    republished = channel.exchanges[MAIN_EXCHANGE].published[0][0]
    assert json.loads(republished.body)["event_id"] == "rabbit-event-1"


def test_mask_sensitive_is_recursive_and_does_not_mutate_source():
    source = {"token": "one", "nested": [{"cvv": "123", "name": "safe"}]}

    masked = mask_sensitive(source)

    assert masked == {
        "token": "***MASKED***",
        "nested": [{"cvv": "***MASKED***", "name": "safe"}],
    }
    assert source["token"] == "one"
