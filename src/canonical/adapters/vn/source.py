from datetime import UTC, date, datetime, time, timedelta, tzinfo
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
        business_timezone: tzinfo = UTC,
    ) -> None:
        self.collection = client[database][collection]
        self.mapping = mapping or VNMapping()
        self.business_timezone = business_timezone

    def get_transactions(
        self,
        region: Region,
        tenant_id: str | None,
        provider: str,
        business_date: date,
    ) -> tuple[CanonicalTransaction, ...]:
        if region is not Region.VN or tenant_id is not None:
            raise ValueError("VN requires region=VN and tenant_id=None")
        if provider.strip().upper() != self.mapping.provider.strip().upper():
            return ()

        start = datetime.combine(
            business_date, time.min, tzinfo=self.business_timezone
        )
        end = start + timedelta(days=1)
        cursor = self.collection.find(
            {
                "CreatedOn": {
                    "$gte": start.astimezone(UTC),
                    "$lt": end.astimezone(UTC),
                }
            }
        )
        return tuple(vn_to_canonical(document, self.mapping) for document in cursor)
