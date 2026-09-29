from dataclasses import dataclass
from contextlib import AbstractContextManager
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Protocol
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
        business_date: date,
    ) -> tuple[CanonicalTransaction, ...]: ...


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
        run_id: UUID | None = None,
    ) -> AbstractContextManager["RunClaim"]: ...

    def complete_run(
        self,
        *,
        claim: "RunClaim",
        system_rows: int,
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
