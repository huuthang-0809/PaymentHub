from datetime import date
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field
from psycopg import Error as DatabaseError

from reconciliation.application import InvalidProviderStatement, RunInProgress
from reconciliation.application.summary import FinancialSummary
from shared_kernel import Region


# Query contracts intentionally expose only committed evidence, never raw payloads.
from datetime import UTC, datetime
from typing import Literal

from fastapi import Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response

from reconciliation.application.query import paginate, result_dict, summary_view
from reconciliation.domain import ReconResultType
from reconciliation.adapters.report import export_report



router = APIRouter(prefix="/recon", tags=["reconciliation"])


class ReconciliationRunResponse(BaseModel):
    run_id: UUID
    job_run_id: UUID
    system_rows: int
    provider_rows: int
    summary: dict[str, int]
    financial_summary: FinancialSummary
    meta: dict[str, datetime] = Field(default_factory=lambda: {"data_as_of": datetime.now(UTC)})


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



def _query(request, operation, **parameters):
    try:
        return request.app.state.query_reconciliation(operation, **parameters)
    except DatabaseError as error:
        raise HTTPException(503, 'QUERY_UNAVAILABLE') from error


def _snapshot(request, run_id, require_completed=False):
    snapshot = _query(request, 'snapshot', run_id=run_id)
    if snapshot is None:
        raise HTTPException(404, 'Run not found')
    if require_completed and snapshot[1] is None:
        raise HTTPException(409, 'Run is not completed')
    return snapshot


def _page(items, limit, cursor, scope, key):
    try:
        return paginate(items, limit, cursor, scope, key)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get('/runs')
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
    filters = dict(region=region.value if region else None, tenant_id=tenant_id,
                   provider=provider.strip().upper() if provider else None,
                   business_date=business_date, status=status)
    rows = _query(request, 'runs', **filters)
    scope = ['runs', *[str(value) for value in filters.values()], sort]
    page, next_cursor = _page(rows, limit, cursor, scope, lambda row: str(row['run_id']))
    return jsonable_encoder({'items': page, 'next_cursor': next_cursor,
                             'meta': {'data_as_of': datetime.now(UTC)}})


@router.get('/runs/{run_id}/summary')
def get_reconciliation_summary(request: Request, run_id: UUID):
    return jsonable_encoder(summary_view(_snapshot(request, run_id)))


@router.get('/runs/{run_id}/results')
def get_reconciliation_results(
    request: Request, run_id: UUID,
    result: ReconResultType | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    sort: Literal['result_key'] = 'result_key',
):
    metadata, stored = _snapshot(request, run_id, True)
    rows = [{'result_key': index, **result_dict(row)}
            for index, row in enumerate(stored.results, 1)
            if result is None or row.result_type == result]
    page, next_cursor = _page(
        rows, limit, cursor, [str(run_id), result.value if result else None, sort],
        lambda row: f"{row['result_key']:020d}",
    )
    return jsonable_encoder({'items': page, 'next_cursor': next_cursor,
                             'meta': {'data_as_of': metadata['finished_at']}})


def _download(request, run_id, format):
    metadata, stored = _snapshot(request, run_id, True)
    try:
        content, media_type = export_report(stored.results, format, metadata['finished_at'])
    except (ValueError, OverflowError, OSError) as error:
        raise HTTPException(500, 'EXPORT_FAILED') from error
    return Response(content, media_type=media_type, headers={
        'Content-Disposition': f'attachment; filename="recon-{run_id}.{format}"',
        'X-Data-As-Of': metadata['finished_at'].isoformat(),
    })


@router.get('/runs/{run_id}/report')
def get_reconciliation_report(
    request: Request, run_id: UUID, format: Literal['csv', 'xlsx'] = 'csv',
):
    return _download(request, run_id, format)


@router.get('/runs/{run_id}/export.parquet')
def get_reconciliation_parquet(request: Request, run_id: UUID):
    return _download(request, run_id, 'parquet')
