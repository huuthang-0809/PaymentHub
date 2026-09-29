from dataclasses import dataclass
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
    def save_run(
        self,
        *,
        run_id: UUID,
        business_date: date,
        region: Region,
        provider: str,
        statement: Path,
        statement_content: bytes,
        provider_txns: list[ProviderTransaction],
        results: list[ReconResult],
    ) -> UUID: ...


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
