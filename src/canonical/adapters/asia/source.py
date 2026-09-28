from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from canonical.adapters.asia.mongo_asia import AsiaMapping, asia_to_canonical
from canonical.domain import CanonicalTransaction
from shared_kernel import Region

class MongoAsiaSource:
    def __init__(
        self, client: Any,
        tenant_databases: Mapping[str, str],
        collection: str,
        mapping: AsiaMapping | None = None
    ) -> None:
        if not tenant_databases or not collection.strip():
            raise ValueError("tenant databases and collection are required")
        resolved_mapping = mapping or AsiaMapping()
        missing_provider = set(tenant_databases) - set(
            resolved_mapping.tenant_providers
        )
        missing_currency = set(tenant_databases) - set(
            resolved_mapping.tenant_currencies
        )
        if missing_provider or missing_currency:
            missing = sorted(missing_provider | missing_currency)
            raise ValueError(f"missing Asia mapping for tenants: {missing}")

        self.client = client
        self.tenant_databases = dict(tenant_databases)
        self.collection = collection.strip()
        self.mapping = resolved_mapping

    def get_transactions(
        self,
        region: Region,
        tenant_id: str | None,
        provider: str,
        business_date: date
    ) -> tuple[CanonicalTransaction, ...]:
        if region != Region.ASIA or tenant_id not in self.tenant_databases:
            raise ValueError("configured Asia tenant is required")
        expected_provider = self.mapping.tenant_providers[tenant_id]
        if provider.strip().upper() != expected_provider.strip().upper():
            return ()
        start = datetime.combine(business_date, time.min, tzinfo=UTC)
        end = start + timedelta(days=1)
        cursor = self.client[self.tenant_databases[tenant_id]][self.collection].find(
            {"CreatedAt" : {"$gte" : start, "$lt": end}}
        )
        return tuple(asia_to_canonical(document, tenant_id, self.mapping) for document in cursor)

    def get_all_tenants(self, business_date: date) -> tuple[CanonicalTransaction, ...]:
        rows: list[CanonicalTransaction] = []
        for tenant_id in sorted(self.tenant_databases):
            rows.extend(
                self.get_transactions(
                    Region.ASIA,
                    tenant_id,
                    self.mapping.tenant_providers[tenant_id],
                    business_date,
                )
            )
        return tuple(rows)
