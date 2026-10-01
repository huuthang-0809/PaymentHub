from datetime import datetime
from typing import Any

from canonical.domain import CanonicalTransaction
from shared_kernel import Region

from .mongo_india import IndiaMapping, india_to_canonical


class MongoIndiaSource:
    def __init__(
        self,
        client: Any,
        database: str,
        collection: str,
        mapping: IndiaMapping | None = None,
        business_timezone: object | None = None,
    ) -> None:
        self.collection = client[database][collection]
        self.mapping = mapping or IndiaMapping()

    def get_transactions(
        self,
        region: Region,
        tenant_id: str | None,
        provider: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> tuple[CanonicalTransaction, ...]:
        if region is not Region.INDIA or tenant_id is not None:
            raise ValueError("India requires region=INDIA and tenant_id=None")
        if provider.strip().upper() != self.mapping.provider.strip().upper():
            return ()

        cursor = self.collection.find(
            {
                "createdAt": {
                    "$gte": start_utc,
                    "$lt": end_utc,
                }
            }
        )
        return tuple(india_to_canonical(document, self.mapping) for document in cursor)
