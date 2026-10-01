"""Inbound HTTP adapter for the reconciliation bounded context."""
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from pydantic import BaseModel, Field

from reconciliation.application import InvalidProviderStatement, RunInProgress
from reconciliation.application.ports import (
    ExportFailed, MatchingRunner, QueryUnavailable, RunNotCompleted, RunNotFound,
)
from reconciliation.application.summary import FinancialSummary
from reconciliation.domain import ReconResultType
from shared_kernel import Region


router = APIRouter(prefix="/reconciliation-runs", tags=["reconciliation"])


class ReconciliationRunResponse(BaseModel):
    run_id: UUID
    job_run_id: UUID
    system_rows: int
    provider_rows: int
    summary: dict[str, int]
    financial_summary: FinancialSummary
    meta: dict[str, datetime] = Field(default_factory=lambda: {"data_as_of": datetime.now(UTC)})


@router.post(
    "",
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
    runner: MatchingRunner = request.app.state.run_reconciliation
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


def _query(operation, **parameters):
    try:
        return operation(**parameters)
    except QueryUnavailable as error:
        raise HTTPException(503, 'QUERY_UNAVAILABLE') from error
    except RunNotFound as error:
        raise HTTPException(404, str(error)) from error
    except RunNotCompleted as error:
        raise HTTPException(409, str(error)) from error
    except ExportFailed as error:
        raise HTTPException(500, 'EXPORT_FAILED') from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get('')
def list_reconciliation_runs(
    request: Request,
    region: Region | None = None,
    tenant_id: str | None = None,
    provider: str | None = None,
    business_date: date | None = None,
    status: Literal['RUNNING', 'FAILED', 'COMPLETED'] | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    sort: Literal['run_id'] = 'run_id',
):
    return jsonable_encoder(_query( #jsonable_encoder help transform dict -> json
        request.app.state.query_reconciliation.runs,
        region=region, tenant_id=tenant_id, provider=provider,
        business_date=business_date, status=status, limit=limit, cursor=cursor, sort=sort,
    ))


@router.get('/{run_id}/summary')
def get_reconciliation_summary(request: Request, run_id: UUID):
    return jsonable_encoder(_query(request.app.state.query_reconciliation.summary, run_id=run_id))


@router.get('/{run_id}/results')
def get_reconciliation_results(
    request: Request, run_id: UUID,
    result: ReconResultType | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    sort: Literal['result_key'] = 'result_key',
):
    return jsonable_encoder(_query(
        request.app.state.query_reconciliation.results,
        run_id=run_id, result=result, limit=limit, cursor=cursor, sort=sort,
    ))


@router.get('/{run_id}/report')
def get_reconciliation_report(
    request: Request, run_id: UUID, format: Literal['csv', 'xlsx', 'parquet'] = 'csv',
):
    content, media_type, data_as_of = _query(
        request.app.state.query_reconciliation.report, run_id=run_id, format=format,
    )
    return Response(content, media_type=media_type, headers={
        'Content-Disposition': f'attachment; filename="recon-{run_id}.{format}"',
        'X-Data-As-Of': data_as_of.isoformat(),
    })

