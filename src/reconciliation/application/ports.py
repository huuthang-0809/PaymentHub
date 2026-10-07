from dataclasses import dataclass
from contextlib import AbstractContextManager
from datetime import date, datetime, time
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, Protocol

if TYPE_CHECKING:
    from .matching import MatchingRunOutput
from uuid import UUID

from canonical.domain import CanonicalTransaction
from reconciliation.domain import ProviderTransaction, ReconResult
from shared_kernel import Region


class SystemTransactionSource(Protocol):
    def get_transactions(
        self,
        region: Region,
        tenant_id: str | None,
        provider: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> tuple[CanonicalTransaction, ...]: ...


class ReconciliationTelemetry(Protocol):
    def record_run( # Measure entire a reconciliation run.
        self,
        *,
        region: Region,
        tenant_id: str | None,
        provider: str,
        outcome: str, #completed, replayed or fail
        duration_seconds: float, # usecase runtime
        system_rows: int | None = None,
        provider_rows: int | None = None,
        summary: Mapping[str, int] | None = None, # classification counts
    ) -> None: ...

    def record_dependency( # Measure a read/write operation within a reconciliation run. Ex: measure read and standardlize process system's transactions.
        self,
        *,
        region: Region,
        tenant_id: str | None,
        provider: str,
        dependency: str, # ex: system source
        operation: str, # ex: get_transactions
        outcome: str,
        duration_seconds: float,
    ) -> None: ...


class ReconRepository(Protocol):
    def claim_run(
        self,
        *,
        business_date: date,
        region: Region,
        tenant_id: str | None,
        provider: str,
        statement: Path,
        statement_content: bytes,
        business_timezone: str,
        cutoff_time: time,
        run_id: UUID | None = None,
    ) -> AbstractContextManager["RunClaim"]: ...

    def complete_run(
        self,
        *,
        claim: "RunClaim",
        system_rows: int,
        provider_rows: int,
        provider_txns: list[ProviderTransaction],
        results: list[ReconResult],
    ) -> None: ...

    def fail_run(self, claim: "RunClaim", error: Exception) -> None: ...


@dataclass(frozen=True, slots=True)
class StoredRun: #Saved run in db
    system_rows: int
    provider_rows: int
    summary: dict[str, int]
    results: tuple[ReconResult, ...]


@dataclass(frozen=True, slots=True)
class RunClaim: # Run info that request is processing
    run_id: UUID
    job_run_id: UUID
    existing: StoredRun | None = None
    business_date: date | None = None


class RunInProgress(Exception): #409 Conflict
    """An identical input is currently owned by another request."""


class ValidationCode(str, Enum):
    MISSING_COLUMN = "MISSING_COLUMN"
    INVALID_TYPE = "INVALID_TYPE"
    INVALID_DATE = "INVALID_DATE"
    UNKNOWN_CURRENCY = "UNKNOWN_CURRENCY"
    NEGATIVE_AMOUNT = "NEGATIVE_AMOUNT"
    EMPTY_ROW = "EMPTY_ROW"
    DUPLICATE_ROW = "DUPLICATE_ROW"
    INVALID_ENCODING = "INVALID_ENCODING"
    INVALID_DELIMITER = "INVALID_DELIMITER"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    row: int | None
    field: str | None
    code: ValidationCode
    message: str


@dataclass(frozen=True, slots=True)
class ParseReport:
    rows: tuple[ProviderTransaction, ...]
    issues: tuple[ValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues


class ProviderStatementParser(Protocol):
    def parse(self, content: bytes, source_file: str) -> ParseReport: ...


class QueryUnavailable(Exception):
    """The committed read store is unavailable."""


class RunNotFound(Exception):
    pass


class RunNotCompleted(Exception):
    pass


class ExportFailed(Exception):
    pass


class ReconQuery(Protocol):
    def runs(self, *, region: str | None = None, tenant_id: str | None = None,
             provider: str | None = None, business_date: date | None = None,
             status: str | None = None) -> list[dict]: ...

    def snapshot(self, run_id: UUID) -> tuple[dict, StoredRun | None] | None: ...

    def results_page(self, run_id: UUID, *, result: str | None = None,
                     after: str | None = None, fetch_limit: int = 51
                     ) -> tuple[dict, list[dict]] | None: ...


class ReportExporter(Protocol):
    def __call__(self, results, format: str, data_as_of) -> tuple[bytes, str]: ...


class MatchingRunner(Protocol):
    def __call__(self, *, statement: Path, statement_content: bytes,
                 business_date: date, region: Region, tenant_id: str | None,
                 provider: str, business_timezone: str, cutoff_time: time,
                 run_id: UUID | None = None) -> "MatchingRunOutput": ...
