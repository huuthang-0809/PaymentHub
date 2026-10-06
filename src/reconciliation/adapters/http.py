"""Inbound HTTP adapter for the reconciliation bounded context."""
import shutil
import tempfile
from datetime import date, time
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from pydantic import BaseModel

from reconciliation.application.ports import (
    ExportFailed, QueryUnavailable, ReconRunDeleter, RunInProgress,
    RunNotCompleted, RunNotFound,
)
from reconciliation.application.calendar import resolve_business_period
from reconciliation.domain import ReconResultType
from shared_kernel import Region


router = APIRouter(prefix="/reconciliation-runs", tags=["reconciliation"])


class ReconciliationAcceptedResponse(BaseModel):
    run_id: UUID
    status: Literal["PENDING"] = "PENDING"


@router.post(
    "",
    response_model=ReconciliationAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_reconciliation_run(
    request: Request,
    statement: Annotated[UploadFile, File()],
    business_date: Annotated[date, Form()],
    region: Annotated[Region, Form()],
    provider: Annotated[str, Form(min_length=1)],
    business_timezone: Annotated[str, Form(min_length=1)],
    cutoff_time: Annotated[time, Form()],
    tenant_id: Annotated[str | None, Form()] = None,
) -> ReconciliationAcceptedResponse:
    with tempfile.NamedTemporaryFile(
        prefix="reconciliation-", suffix=".csv", delete=False
    ) as temporary:
        shutil.copyfileobj(statement.file, temporary) # copy upload into NamedTemporaryFile
        statement_path = Path(temporary.name)

    if statement_path.stat().st_size == 0:
        statement_path.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "empty statement")

    filename = Path(statement.filename or "provider-statement.csv").name
    normalized_provider = provider.strip().upper()
    run_id = uuid4()
    try:
        period = resolve_business_period(
            business_date, business_timezone, cutoff_time
        )
        run_id = request.app.state.submit_reconciliation(
            statement=Path(filename),
            statement_path=statement_path,
            business_date=business_date,
            region=region,
            tenant_id=tenant_id,
            provider=normalized_provider,
            business_timezone=period.timezone,
            cutoff_time=period.cutoff_time,
            run_id=run_id,
        )
    except ValueError as exc:
        statement_path.unlink(missing_ok=True)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
        ) from exc
    except Exception:
        statement_path.unlink(missing_ok=True)
        raise

    return ReconciliationAcceptedResponse(run_id=run_id)


def _query(operation, **parameters):
    try:
        return operation(**parameters)
    except QueryUnavailable as error:
        raise HTTPException(503, 'QUERY_UNAVAILABLE') from error
    except RunNotFound as error:
        raise HTTPException(404, str(error)) from error
    except RunNotCompleted as error:
        raise HTTPException(409, str(error)) from error
    except RunInProgress as error:
        raise HTTPException(409, str(error)) from error
    except ExportFailed as error:
        raise HTTPException(500, 'EXPORT_FAILED') from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.delete('/{run_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_reconciliation_run(request: Request, run_id: UUID) -> Response:
    deleter: ReconRunDeleter = request.app.state.delete_reconciliation_run
    _query(deleter, run_id=run_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get('')
def list_reconciliation_runs(
    request: Request,
    run_id: UUID | None = None,
    region: Region | None = None,
    tenant_id: str | None = None,
    provider: str | None = None,
    business_date: date | None = None,
    status: Literal['PENDING', 'RUNNING', 'FAILED', 'COMPLETED'] | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    sort: Literal['run_id'] = 'run_id',
):
    return jsonable_encoder(_query( #jsonable_encoder help transform dict -> json
        request.app.state.query_reconciliation.runs,
        run_id=run_id, region=region, tenant_id=tenant_id, provider=provider,
        business_date=business_date, status=status, limit=limit, cursor=cursor, sort=sort,
    ))


@router.get('/{run_id}/summary')
def get_reconciliation_summary(request: Request, run_id: UUID):
    return jsonable_encoder(_query(request.app.state.query_reconciliation.summary, run_id=run_id))


@router.get('/{run_id}/results')
def get_reconciliation_results(
    request: Request, run_id: UUID,
    result: ReconResultType | Literal['ALL'] | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    sort: Literal['result_key'] = 'result_key',
):
    return jsonable_encoder(_query(
        request.app.state.query_reconciliation.results,
        run_id=run_id, result=None if result == 'ALL' else result,
        limit=limit, cursor=cursor, sort=sort,
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
