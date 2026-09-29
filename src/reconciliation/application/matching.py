from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

from reconciliation.domain import MatchingEngine, ReconResult
from shared_kernel import Region

from .ports import ProviderStatementParser, ReconRepository, SystemTransactionSource


@dataclass(frozen=True, slots=True)
class MatchingRunOutput:
    run_id: UUID
    job_run_id: UUID
    system_rows: int
    provider_rows: int
    summary: dict[str, int]
    results: tuple[ReconResult, ...]


class InvalidProviderStatement(ValueError):
    """Raised when a provider statement cannot enter the matching pipeline."""


class RunMatching:
    def __init__(
        self,
        system_source: SystemTransactionSource,
        parser: ProviderStatementParser,
        repository: ReconRepository,
    ) -> None:
        self.system_source = system_source
        self.parser = parser
        self.repository = repository

    def execute(
        self,
        *,
        statement: Path,
        statement_content: bytes,
        business_date: date,
        region: Region,
        tenant_id: str | None,
        provider: str,
        run_id: UUID | None = None,
    ) -> MatchingRunOutput:
        report = self.parser.parse(statement_content, statement.name)
        if not report.valid:
            details = "; ".join(
                f"row={issue.row}, field={issue.field}: {issue.message}"
                for issue in report.issues
            )
            raise InvalidProviderStatement(details)

        normalized_provider = provider.strip().upper()
        if not normalized_provider:
            raise ValueError("provider is required")

        system_transactions = self.system_source.get_transactions(
            region,
            tenant_id,
            normalized_provider,
            business_date,
        )
        system_rows = [
            transaction
            for transaction in system_transactions
            if not transaction.is_deleted and not transaction.is_test_data
        ]
        provider_rows = list(report.rows)
        resolved_run_id = run_id or uuid4()
        results = MatchingEngine().match(
            str(resolved_run_id), provider_rows, system_rows
        )
        job_run_id = self.repository.save_run(
            run_id=resolved_run_id,
            business_date=business_date,
            region=region,
            provider=normalized_provider,
            statement=statement,
            statement_content=statement_content,
            provider_txns=provider_rows,
            results=results,
        )
        summary = Counter(result.result_type.value for result in results)

        return MatchingRunOutput(
            run_id=resolved_run_id,
            job_run_id=job_run_id,
            system_rows=len(system_rows),
            provider_rows=len(provider_rows),
            summary=dict(sorted(summary.items())),
            results=tuple(results),
        )
