from datetime import datetime
from typing import Any

from canonical.domain import CanonicalTransaction
from shared_kernel import Region

from .mongo_vn import VNMapping, vn_to_canonical


class MongoVnSource:
    def __init__(
        self,
        client: Any,
        database: str,
        collection: str,
        mapping: VNMapping | None = None,
        business_timezone: object | None = None,
    ) -> None:
        self.collection = client[database][collection]
        self.mapping = mapping or VNMapping()

    def get_transactions(
        self,
        region: Region,
        tenant_id: str | None,
        provider: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> tuple[CanonicalTransaction, ...]:
        if region is not Region.VN or tenant_id is not None:
            raise ValueError("VN requires region=VN and tenant_id=None")
        if provider.strip().upper() != self.mapping.provider.strip().upper():
            return ()

        cursor = self.collection.find(
            {
                "CreatedOn": {
                    "$gte": start_utc,
                    "$lt": end_utc,
                }
            }
        )
        # Reconciliation only needs normalized fields. Keeping each Mongo
        # document in `raw` would retain hundreds of thousands of dictionaries.
        return tuple(
            vn_to_canonical(document, self.mapping, include_raw=False)
            for document in cursor
        )
