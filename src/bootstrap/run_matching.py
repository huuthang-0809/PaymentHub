from datetime import date
from pathlib import Path
from uuid import UUID

from reconciliation.adapters import CsvFixtureParser
from reconciliation.application import (
    MatchingRunOutput,
    ReconRepository,
    RunMatching,
    SystemTransactionSource,
)
from shared_kernel import Region


def run_matching(
    system_source: SystemTransactionSource,
    *,
    statement: Path,
    business_date: date,
    region: Region,
    tenant_id: str | None,
    provider: str,
    repository: ReconRepository,
    run_id: UUID | None = None,
) -> MatchingRunOutput:
    """Wire concrete adapters and invoke the matching application use case."""
    use_case = RunMatching(system_source, CsvFixtureParser(), repository)
    return use_case.execute(
        statement=statement,
        statement_content=statement.read_bytes(),
        business_date=business_date,
        region=region,
        tenant_id=tenant_id,
        provider=provider,
        run_id=run_id,
    )
