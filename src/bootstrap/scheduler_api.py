"""In-memory generation and simulation jobs for the scheduler service."""

import os
import time
from datetime import date, timedelta
from pathlib import Path
from threading import Lock
from typing import Callable, Literal
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException
from prometheus_client import make_asgi_app
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bootstrap.scheduler import _emit, virtual_times
from reconciliation.adapters.daily_scheduler import (
    DailyReconciliationScheduler,
    HttpReconciliationSubmitter,
    load_schedules,
)
from tests.data_generator.month_simulation import generate_data


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    days: int = Field(default=1, gt=0)

    @model_validator(mode="after")
    def validate_date_range(self):
        try:
            self.start_date + timedelta(days=self.days - 1)
        except OverflowError as error:
            raise ValueError("Simulation date range is too large") from error
        return self


class GenerationRequest(JobRequest):
    records_per_region: int = Field(default=100, ge=2)
    missing_statement_count: int = Field(default=0, ge=0, le=2)

    @model_validator(mode="after")
    def validate_missing_count(self):
        if self.missing_statement_count > self.days:
            raise ValueError("missing_statement_count must not exceed days")
        return self


class SimulationRequest(JobRequest):
    seconds_per_day: float = Field(default=2, ge=0, allow_inf_nan=False)
    worker_count: int = Field(
        default_factory=lambda: int(os.getenv("RECON_WORKER_COUNT", "1")), gt=0,
        validate_default=True,
    )


class JobStatus(BaseModel):
    job_id: str
    status: Literal["running", "completed", "failed"]
    error: str | None = None


def run_generation(request: GenerationRequest) -> None:
    generate_data(
        start=request.start_date,
        days=request.days,
        records=request.records_per_region,
        missing_statement_count=request.missing_statement_count,
        statement_directory=Path(os.getenv("RECON_STATEMENT_DIR", "/statements")),
    )


def run_simulation(request: SimulationRequest) -> None:
    schedules = load_schedules(os.getenv("RECON_SCHEDULES_JSON", "[]"))
    if not schedules:
        raise ValueError("RECON_SCHEDULES_JSON must contain at least one schedule")
    failed = False

    def emit(event: str, context: dict[str, object]) -> None:
        nonlocal failed
        _emit(event, context)
        # run_due logs submission errors instead of raising them.
        if event == "run_failed":
            failed = True

    # Each job gets a fresh completed set, even when dates repeat.
    scheduler = DailyReconciliationScheduler(
        Path(os.getenv("RECON_STATEMENT_DIR", "/statements")),
        schedules,
        HttpReconciliationSubmitter(
            os.getenv("RECON_API_URL", "http://api:8000"),
            float(os.getenv("RECON_API_TIMEOUT_SECONDS", "30")),
            float(os.getenv("RECON_STATUS_POLL_SECONDS", "1")),
            float(os.getenv("RECON_STATUS_TIMEOUT_SECONDS", "3600")),
        ),
        emit,
        worker_count=request.worker_count,
    )
    for index, now in enumerate(virtual_times(request.start_date, request.days)):
        scheduler.run_due(now)
        _emit("virtual_day_completed", {"business_date": now.date().isoformat(), "day": index + 1})
        if index + 1 < request.days:
            time.sleep(request.seconds_per_day)
    if failed:
        raise RuntimeError("One or more reconciliation runs failed; check scheduler logs")


def create_app() -> FastAPI:
    app = FastAPI(title="PaymentHub Scheduler")
    app.mount("/metrics", make_asgi_app())
    jobs: dict[tuple[str, str], JobStatus] = {}
    lock = Lock()

    def execute(kind: str, job_id: str, action: Callable[[], None]) -> None:
        error = None
        try:
            action()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        with lock:
            jobs[kind, job_id] = JobStatus(
                job_id=job_id, status="failed" if error else "completed", error=error,
            )
        _emit("job_finished", {"kind": kind, **jobs[kind, job_id].model_dump()})

    def submit(kind: str, background: BackgroundTasks, action: Callable[[], None]) -> JobStatus:
        with lock:
            # Generation must not change files while simulation reads them.
            if any(job.status == "running" for job in jobs.values()):
                raise HTTPException(409, "A generation or simulation job is already running")
            job = JobStatus(job_id=str(uuid4()), status="running")
            jobs[kind, job.job_id] = job
        background.add_task(execute, kind, job.job_id, action)
        return job

    def get_job(kind: str, job_id: str) -> JobStatus:
        with lock:
            job = jobs.get((kind, job_id))
        if job is None:
            raise HTTPException(404, "Job not found")
        return job

    @app.post("/generation-jobs", status_code=202, response_model=JobStatus)
    async def generation(request: GenerationRequest, background: BackgroundTasks):
        return submit("generation", background, lambda: run_generation(request))

    @app.get("/generation-jobs/{job_id}", response_model=JobStatus)
    async def generation_status(job_id: str):
        return get_job("generation", job_id)

    @app.get("/generation-jobs", response_model=list[JobStatus])
    async def generation_jobs():
        with lock:
            return [job for (kind, _), job in reversed(jobs.items()) if kind == "generation"]

    @app.post("/simulation-jobs", status_code=202, response_model=JobStatus)
    async def simulation(request: SimulationRequest, background: BackgroundTasks):
        return submit("simulation", background, lambda: run_simulation(request))

    @app.get("/simulation-jobs/{job_id}", response_model=JobStatus)
    async def simulation_status(job_id: str):
        return get_job("simulation", job_id)

    @app.get("/simulation-jobs", response_model=list[JobStatus])
    async def simulation_jobs():
        with lock:
            return [job for (kind, _), job in reversed(jobs.items()) if kind == "simulation"]

    return app


app = create_app()
