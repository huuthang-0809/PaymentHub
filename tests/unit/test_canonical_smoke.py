from datetime import datetime, timezone
import importlib
from pathlib import Path
import pytest
from canonical.domain import CanonicalTransaction
from shared_kernel import Region, TxnType, CanonicalStatus, AmountStatus


def transaction(**changes):
    values = dict(region=Region.VN, tenant_id=None, txn_id="txn-1",
                  txn_type=TxnType.PAYMENT, provider=" onepay ",
                  status=CanonicalStatus.SUCCESS, amount_status=AmountStatus.OK,
                  created_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
                  is_terminal=True, is_money_movement=True, is_reconcilable=True)
    values.update(changes)
    return CanonicalTransaction(**values)


@pytest.mark.parametrize("field,value", [("provider", ""), ("provider", "  "), ("txn_id", "")])
def test_canonical_rejects_empty_identity(field, value):
    with pytest.raises(ValueError):
        transaction(**{field: value})


def test_canonical_optional_values_and_normalization():
    txn = transaction()
    assert txn.provider == "ONEPAY"
    assert txn.provider_txn_id is None
    assert txn.updated_at is None
    assert transaction(provider_txn_id=" id ").provider_txn_id == "id"


def test_canonical_rejects_naive_timestamp():
    with pytest.raises(ValueError):
        transaction(created_at=datetime(2026, 9, 22))


def test_canonical_rejects_boolean_amount():
    with pytest.raises(TypeError):
        transaction(amount_minor=True)


def test_every_source_module_imports():
    source = Path(__file__).resolve().parents[2] / "src"
    failures = []
    for path in sorted(source.rglob("*.py")):
        parts = list(path.relative_to(source).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        if parts:
            name = ".".join(parts)
            try:
                importlib.import_module(name)
            except Exception as error:
                failures.append(f"{name}: {type(error).__name__}: {error}")
    assert not failures, "\n".join(failures)


def test_health_and_legacy_alias():
    import asyncio
    import json
    from bootstrap.main import app

    async def request(path):
        messages = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            messages.append(message)
        await app({"type": "http", "asgi": {"version": "3.0"},
                   "http_version": "1.1", "method": "GET", "scheme": "http",
                   "path": path, "raw_path": path.encode(), "query_string": b"",
                   "root_path": "", "headers": [], "server": ("test", 80),
                   "client": ("test", 1234)}, receive, send)
        assert next(m["status"] for m in messages if m["type"] == "http.response.start") == 200
        body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        assert json.loads(body)["status"] == "ok"
    for path in ("/health", "/heath"):
        asyncio.run(request(path))
