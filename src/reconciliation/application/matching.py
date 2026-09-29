from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID

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
        normalized_provider = provider.strip().upper()
        if not normalized_provider:
            raise ValueError("provider is required")
        with self.repository.claim_run(
            business_date=business_date,
            region=region,
            tenant_id=tenant_id,
            provider=normalized_provider,
            statement=statement,
            statement_content=statement_content,
            run_id=run_id,
        ) as claim:
            if claim.existing is not None: # If run not exist -> matching
                return MatchingRunOutput(
                    claim.run_id, claim.job_run_id,
                    claim.existing.system_rows, claim.existing.provider_rows,
                    claim.existing.summary, claim.existing.results,
                )
            try:
                report = self.parser.parse(statement_content, statement.name)
                if not report.valid:
                    details = "; ".join(
                        f"row={issue.row}, field={issue.field}: {issue.message}"
                        for issue in report.issues
                    )
                    raise InvalidProviderStatement(details) #Raise exception with detail issues

                provider_rows = list(report.rows)
                out_of_scope = next(
                    (
                        transaction
                        for transaction in provider_rows
                        if transaction.region is not region
                        or transaction.tenant_id != tenant_id
                        or transaction.provider != normalized_provider
                    ),
                    None,
                )
                if out_of_scope is not None:
                    raise InvalidProviderStatement(
                        f"row={out_of_scope.source_row_number}: statement row is outside "
                        "the requested region, tenant, or provider"
                    )

                system_transactions = self.system_source.get_transactions(
                    region, tenant_id, normalized_provider, business_date,
                )
                system_rows = [
                    transaction for transaction in system_transactions
                    if not transaction.is_deleted and not transaction.is_test_data
                ]
                results = MatchingEngine().match(
                    str(claim.run_id), provider_rows, system_rows
                )
                self.repository.complete_run(
                    claim=claim, system_rows=len(system_rows),
                    provider_txns=provider_rows, results=results,
                )
                summary = Counter(result.result_type.value for result in results)
                return MatchingRunOutput(
                    claim.run_id, claim.job_run_id, len(system_rows),
                    len(provider_rows), dict(sorted(summary.items())), tuple(results),
                )
            except Exception as error:
                self.repository.fail_run(claim, error)
                raise
