from datetime import date
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from reconciliation.application import InvalidProviderStatement, RunInProgress
from reconciliation.application.summary import FinancialSummary
from shared_kernel import Region


router = APIRouter(prefix="/recon", tags=["reconciliation"])


class ReconciliationRunResponse(BaseModel):
    run_id: UUID
    job_run_id: UUID
    system_rows: int
    provider_rows: int
    summary: dict[str, int]
    financial_summary: FinancialSummary


@router.post(
    "/runs",
    response_model=ReconciliationRunResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_reconciliation_run(
    request: Request,
    statement: Annotated[UploadFile, File()],
    business_date: Annotated[date, Form()],
    region: Annotated[Region, Form()],
    provider: Annotated[str, Form(min_length=1)],
    tenant_id: Annotated[str | None, Form()] = None,
) -> ReconciliationRunResponse:
    content = statement.file.read()
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "empty statement")

    filename = Path(statement.filename or "provider-statement.csv").name
    runner: Any = request.app.state.run_reconciliation
    try:
        output = runner(
            statement=Path(filename),
            statement_content=content,
            business_date=business_date,
            region=region,
            tenant_id=tenant_id,
            provider=provider,
        )
    except InvalidProviderStatement as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
        ) from exc
    except RunInProgress as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
        ) from exc

    return ReconciliationRunResponse(
        run_id=output.run_id,
        job_run_id=output.job_run_id,
        system_rows=output.system_rows,
        provider_rows=output.provider_rows,
        summary=output.summary,
        financial_summary=output.financial_summary,
    )
