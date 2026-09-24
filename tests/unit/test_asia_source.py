from datetime import UTC, date, datetime

import pytest

from canonical.adapters.asia import AsiaMapping, MongoAsiaSource
from shared_kernel import Region


class Collection:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents
        self.last_query: dict[str, object] | None = None

    def find(self, query: dict[str, object]) -> list[dict[str, object]]:
        self.last_query = query
        return self.documents


def document(currency: str) -> dict[str, object]:
    return {
        "_id": "same-id",
        "CreatedAt": datetime(2026, 9, 21, tzinfo=UTC),
        "Type": 1,
        "Status": 2,
        "Currency": currency,
        "AmountAsDecimal": "10",
        "Amount": "10",
    }


def source_fixture() -> tuple[MongoAsiaSource, Collection, Collection]:
    indonesia = Collection([document("IDR")])
    philippines = Collection([document("PHP")])
    client = {
        "db_id": {"transactions": indonesia},
        "db_ph": {"transactions": philippines},
    }
    source = MongoAsiaSource(
        client,
        {"asia-id": "db_id", "asia-ph": "db_ph"},
        "transactions",
        AsiaMapping(),
    )
    return source, indonesia, philippines


def test_all_tenants_are_read_and_kept_distinct() -> None:
    source, _, _ = source_fixture()
    rows = source.get_all_tenants(date(2026, 9, 21))

    assert len(rows) == 2
    assert rows[0].global_key != rows[1].global_key
    assert {row.tenant_id for row in rows} == {"asia-id", "asia-ph"}


def test_query_uses_half_open_utc_business_day() -> None:
    source, indonesia, _ = source_fixture()
    source.get_transactions(
        Region.ASIA, "asia-id", " espay ", date(2026, 9, 21)
    )

    assert indonesia.last_query == {
        "CreatedAt": {
            "$gte": datetime(2026, 9, 21, tzinfo=UTC),
            "$lt": datetime(2026, 9, 22, tzinfo=UTC),
        }
    }


def test_provider_outside_the_tenant_scope_returns_no_rows() -> None:
    source, _, _ = source_fixture()
    rows = source.get_transactions(
        Region.ASIA, "asia-id", "2C2P", date(2026, 9, 21)
    )
    assert rows == ()


@pytest.mark.parametrize(
    ("region", "tenant_id"),
    [(Region.VN, "asia-id"), (Region.ASIA, None), (Region.ASIA, "missing")],
)
def test_source_rejects_an_invalid_scope(
    region: Region, tenant_id: str | None
) -> None:
    source, _, _ = source_fixture()
    with pytest.raises(ValueError, match="configured Asia tenant"):
        source.get_transactions(region, tenant_id, "ESPAY", date(2026, 9, 21))


def test_source_rejects_tenant_without_complete_mapping() -> None:
    with pytest.raises(ValueError, match="missing Asia mapping"):
        MongoAsiaSource(
            {},
            {"new-tenant": "new-db"},
            "transactions",
            AsiaMapping(),
        )
