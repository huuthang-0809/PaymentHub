"""Mongo snapshot adapter for deterministic synthetic batches."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from canonical.adapters.asia import asia_to_canonical
from canonical.adapters.config import paymenthub_asia_mapping, paymenthub_vn_mapping
from canonical.adapters.india import india_to_canonical
from canonical.adapters.vn import vn_to_canonical
from canonical.domain import CanonicalTransaction
from monitoring.domain import CanonicalEvent
from shared_kernel import AmountStatus, Region


COLLECTION = "payment_transactions"


@dataclass(frozen=True, slots=True)
class MongoBatchScope:
    database: str
    region: Region
    tenant_id: str | None


DEFAULT_SCOPES = (
    MongoBatchScope("paymenthub_vn", Region.VN, None),
    MongoBatchScope("paymenthub_asia_id", Region.ASIA, "asia-id"),
    MongoBatchScope("paymenthub_asia_ph", Region.ASIA, "asia-ph"),
    MongoBatchScope("paymenthub_india", Region.INDIA, None),
)


@dataclass(frozen=True, slots=True)
class MappingFailure:
    database: str
    source_id: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class MongoBatchResult:
    batch_id: str
    events: tuple[CanonicalEvent, ...]
    failures: tuple[MappingFailure, ...]
    documents_by_region: Mapping[Region, int]

    @property
    def read(self) -> int:
        return sum(self.documents_by_region.values())


class MongoCanonicalBatchSource:
    def __init__(
        self,
        client: Any,
        *,
        collection: str = COLLECTION,
        scopes: tuple[MongoBatchScope, ...] = DEFAULT_SCOPES,
    ) -> None:
        if not collection.strip() or not scopes:
            raise ValueError("collection and scopes are required")
        self.client = client
        self.collection = collection.strip()
        self.scopes = scopes
        self.vn_mapping = paymenthub_vn_mapping()
        self.asia_mapping = paymenthub_asia_mapping()

    def _mapper(
        self, scope: MongoBatchScope
    ) -> Callable[[Mapping[str, Any]], CanonicalTransaction]:
        if scope.region is Region.VN:
            return lambda document: vn_to_canonical(document, self.vn_mapping)
        if scope.region is Region.ASIA:
            if scope.tenant_id is None:
                raise ValueError("Asia batch scope requires tenant_id")
            return lambda document: asia_to_canonical(
                document, scope.tenant_id, self.asia_mapping
            )
        if scope.region is Region.INDIA:
            return india_to_canonical
        raise ValueError(f"unsupported region: {scope.region.value}")

    def read(self, batch_id: str) -> MongoBatchResult:
        if not batch_id.strip():
            raise ValueError("batch_id is required")
        events: list[CanonicalEvent] = []
        failures: list[MappingFailure] = []
        counts = {region: 0 for region in Region}

        for scope in self.scopes:
            mapper = self._mapper(scope)
            cursor = self.client[scope.database][self.collection].find(
                {"testBatchId": batch_id}
            ).sort("_id", 1)
            for document in cursor:
                counts[scope.region] += 1
                source_id = str(document.get("_id", "<missing>"))
                try:
                    transaction = mapper(document)
                except (TypeError, ValueError, KeyError) as error:
                    failures.append(
                        MappingFailure(
                            database=scope.database,
                            source_id=source_id,
                            code="MAPPING_VALIDATION",
                            message=str(error)[:300],
                        )
                    )
                    continue
                except Exception as error:
                    failures.append(
                        MappingFailure(
                            database=scope.database,
                            source_id=source_id,
                            code="MAPPING_UNEXPECTED",
                            message=f"{type(error).__name__}: {str(error)[:250]}",
                        )
                    )
                    continue
                events.append(
                    CanonicalEvent(
                        event_id=f"{batch_id}|{scope.database}|{source_id}",
                        transaction=transaction,
                        parse_error=transaction.amount_status is AmountStatus.INVALID,
                    )
                )

        return MongoBatchResult(
            batch_id=batch_id,
            events=tuple(events),
            failures=tuple(failures),
            documents_by_region=counts,
        )
