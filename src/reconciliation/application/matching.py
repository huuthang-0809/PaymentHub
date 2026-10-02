from collections import Counter
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from uuid import UUID

from reconciliation.domain import MatchingEngine, ProviderTransaction, ReconResult
from shared_kernel import Region

from .ports import (
    ProviderStatementParser,
    ReconciliationTelemetry,
    ReconRepository,
    RunInProgress,
    SystemTransactionSource,
)
from .summary import FinancialSummary, build_financial_summary
from .calendar import classify_provider_rows, resolve_business_period


@dataclass(frozen=True, slots=True)
class MatchingRunOutput:
    run_id: UUID
    job_run_id: UUID
    system_rows: int
    provider_rows: int
    summary: dict[str, int]
    results: tuple[ReconResult, ...]
    replayed: bool = False #new run or replay, True: get old results

    @property
    def financial_summary(self) -> FinancialSummary:
        return build_financial_summary(self.results)


class InvalidProviderStatement(ValueError):
    """Raised when a provider statement cannot enter the matching pipeline."""


class RunMatching:
    def __init__(
        self,
        system_source: SystemTransactionSource,
        parser: ProviderStatementParser,
        repository: ReconRepository,
        telemetry: ReconciliationTelemetry | None = None,
    ) -> None:
        self.system_source = system_source
        self.parser = parser
        self.repository = repository
        self.telemetry = telemetry

    def execute(
        self,
        *,
        statement: Path,
        statement_content: bytes,
        business_date: date,
        region: Region,
        tenant_id: str | None,
        provider: str,
        business_timezone: str,
        cutoff_time: time,
        run_id: UUID | None = None,
    ) -> MatchingRunOutput:
        started = perf_counter()
        outcome = "failed" #default status
        output: MatchingRunOutput | None = None
        normalized_provider = provider.strip().upper()
        try:
            output = self._execute(
                statement=statement,
                statement_content=statement_content,
                business_date=business_date,
                region=region,
                tenant_id=tenant_id,
                provider=normalized_provider,
                business_timezone=business_timezone,
                cutoff_time=cutoff_time,
                run_id=run_id,
            )
            outcome = "replayed" if output.replayed else "completed"
            return output
        except InvalidProviderStatement:
            outcome = "invalid_statement"
            raise
        except RunInProgress:
            outcome = "in_progress"
            raise
        finally:
            if self.telemetry is not None:
                financial = output.financial_summary if output else None
                self.telemetry.record_run(
                    region=region,
                    tenant_id=tenant_id,
                    provider=normalized_provider,
                    outcome=outcome,
                    duration_seconds=perf_counter() - started,
                    system_rows=output.system_rows if output else None,
                    provider_rows=output.provider_rows if output else None,
                    summary=output.summary if output else None,
                    currency_deltas=(
                        {
                            total.currency: total.delta_amount_minor
                            for total in financial.totals_by_currency
                            if total.currency is not None
                            and total.delta_amount_minor is not None
                        }
                        if financial else None
                    ),
                )

    def _execute(
        self,
        *,
        statement: Path,
        statement_content: bytes,
        business_date: date,
        region: Region,
        tenant_id: str | None,
        provider: str,
        business_timezone: str,
        cutoff_time: time,
        run_id: UUID | None,
    ) -> MatchingRunOutput:
        if not provider:
            raise ValueError("provider is required")
        period = resolve_business_period(
            business_date, business_timezone, cutoff_time
        )
        with self.repository.claim_run(
            business_date=business_date,
            region=region,
            tenant_id=tenant_id,
            provider=provider,
            statement=statement,
            statement_content=statement_content,
            business_timezone=period.timezone,
            cutoff_time=period.cutoff_time,
            run_id=run_id,
        ) as claim:
            if claim.existing is not None: # If run not exist -> matching
                return MatchingRunOutput(
                    claim.run_id, claim.job_run_id,
                    claim.existing.system_rows, claim.existing.provider_rows,
                    claim.existing.summary, claim.existing.results, True,
                )
            try:
                report = self.parser.parse(statement_content, statement.name)
                if not report.valid:
                    details = "; ".join(
                        f"row={issue.row}, field={issue.field}: {issue.message}"
                        for issue in report.issues
                    )
                    raise InvalidProviderStatement(details) #Raise exception with detail issues

                parsed_provider_rows = list(report.rows)
                out_of_scope = next(
                    (
                        transaction
                        for transaction in parsed_provider_rows
                        if transaction.region is not region
                        or transaction.tenant_id != tenant_id
                        or transaction.provider != provider
                    ),
                    None,
                )
                if out_of_scope is not None:
                    raise InvalidProviderStatement(
                        f"row={out_of_scope.source_row_number}: statement row is outside "
                        "the requested region, tenant, or provider"
                    )

                current_provider_rows, persisted_provider_rows = classify_provider_rows(
                    parsed_provider_rows, period
                )
                carried_rows = self._dependency(
                    lambda: self.repository.late_provider_transactions(
                        business_date=business_date,
                        region=region,
                        tenant_id=tenant_id,
                        provider=provider,
                        start_utc=period.start_utc,
                        end_utc=period.end_utc,
                    ),
                    dependency="repository",
                    operation="late_provider_transactions",
                    region=region,
                    tenant_id=tenant_id,
                    provider=provider,
                )
                provider_rows = _merge_provider_rows(
                    current_provider_rows, list(carried_rows)
                )
                persisted_provider_rows.extend(
                    provider_rows[len(current_provider_rows):]
                )
                system_transactions = self._dependency(
                    lambda: self.system_source.get_transactions(
                        region, tenant_id, provider,
                        period.start_utc, period.end_utc,
                    ),
                    dependency="system_source",
                    operation="get_transactions",
                    region=region,
                    tenant_id=tenant_id,
                    provider=provider,
                )
                system_rows = [
                    transaction for transaction in system_transactions
                    if not transaction.is_deleted and not transaction.is_test_data
                ]
                results = MatchingEngine().match(
                    str(claim.run_id), provider_rows, system_rows
                )
                self._dependency(
                    self.repository.complete_run,
                    dependency="repository",
                    operation="complete_run",
                    region=region,
                    tenant_id=tenant_id,
                    provider=provider,
                    claim=claim, system_rows=len(system_rows),
                    provider_rows=len(provider_rows),
                    provider_txns=persisted_provider_rows, results=results,
                )
                summary = Counter(result.result_type.value for result in results)
                return MatchingRunOutput(
                    claim.run_id, claim.job_run_id, len(system_rows),
                    len(provider_rows), dict(sorted(summary.items())), tuple(results),
                )
            except Exception as error:
                self._dependency(
                    self.repository.fail_run,
                    dependency="repository",
                    operation="fail_run",
                    region=region,
                    tenant_id=tenant_id,
                    provider=provider,
                    claim=claim,
                    error=error,
                )
                raise

    def _dependency(
        self,
        callback: Callable[..., Any],
        *,
        dependency: str,
        operation: str,
        region: Region,
        tenant_id: str | None,
        provider: str,
        **parameters: object,
    ) -> Any:
        started = perf_counter()
        outcome = "failed"
        try:
            result = callback(**parameters)
            outcome = "success"
            return result
        finally:
            if self.telemetry is not None:
                self.telemetry.record_dependency(
                    region=region,
                    tenant_id=tenant_id,
                    provider=provider,
                    dependency=dependency,
                    operation=operation,
                    outcome=outcome,
                    duration_seconds=perf_counter() - started,
                )


def _provider_identity(transaction: ProviderTransaction) -> tuple | None:
    provider_txn_id = transaction.provider_txn_id
    if transaction.region is Region.ASIA and transaction.txn_type.value == "REFUND":
        provider_txn_id = None
    if provider_txn_id:
        key_name, identifier = "provider_txn_id", provider_txn_id
    elif transaction.region is Region.ASIA and transaction.provider_ref_id:
        key_name, identifier = "provider_ref_id", transaction.provider_ref_id
    elif transaction.merchant_ref:
        key_name, identifier = "merchant_ref", transaction.merchant_ref
    else:
        return None
    return (
        transaction.region,
        transaction.tenant_id,
        transaction.provider,
        transaction.currency,
        key_name,
        identifier,
    )


def _merge_provider_rows(
    current_rows: list[ProviderTransaction],
    carried_rows: list[ProviderTransaction],
) -> list[ProviderTransaction]:
    merged = list(current_rows)
    origins = {(row.source_file, row.source_row_number) for row in current_rows}
    identities = {
        identity
        for row in current_rows
        if (identity := _provider_identity(row)) is not None
    }
    for row in carried_rows:
        origin = (row.source_file, row.source_row_number)
        identity = _provider_identity(row)
        if origin in origins or (identity is not None and identity in identities):
            continue
        merged.append(row)
        origins.add(origin)
        if identity is not None:
            identities.add(identity)
    return merged
