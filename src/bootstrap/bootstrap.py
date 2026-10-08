import hashlib
import logging
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from pymongo import MongoClient
from psycopg import connect
from psycopg_pool import ConnectionPool

from canonical.adapters.asia import AsiaMapping, MongoAsiaSource
from canonical.adapters.india import MongoIndiaSource
from canonical.adapters.vn import MongoVnSource, VNMapping
from reconciliation.adapters import CsvFixtureParser, PostgreReconRepository
from reconciliation.adapters.metrics import ReconciliationMetrics
from reconciliation.adapters.daily_scheduler import load_schedules
from reconciliation.adapters.freshness import FreshnessService
from reconciliation.application.query import QueryReconciliation
from reconciliation.adapters.query import PooledReconQuery, PooledReconRunDeleter
from reconciliation.adapters.report import export_report
from reconciliation.application import RunMatching
from shared_kernel import Region
from shared_kernel.enums import Instrument


reconciliation_metrics = ReconciliationMetrics()
logger = logging.getLogger(__name__)


class _ProcessTelemetry:
    """Send small metric events back to the API process."""

    def __init__(self, connection) -> None:
        self.connection = connection

    def record_run(self, **values) -> None:
        self.connection.send(("record_run", values))

    def record_dependency(self, **values) -> None:
        self.connection.send(("record_dependency", values))


def _file_checksum(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def _database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://paymenthub:paymenthub@localhost:5432/paymenthub",
    ).replace("postgresql+psycopg://", "postgresql://", 1)


def _asia_mapping() -> AsiaMapping:
    return AsiaMapping(
        instrument_code={
            "card": Instrument.CARD,
            "qr": Instrument.QR,
            "virtual-account": Instrument.VIRTUAL_ACCOUNT,
        },
        synthetic_flag_value={"synthetic": True},
    )


def _business_timezone_name(region: Region, tenant_id: str | None) -> str:
    if region is Region.VN:
        return os.getenv("MONGO_VN_TIMEZONE", "Asia/Ho_Chi_Minh")
    if region is Region.INDIA:
        return os.getenv("MONGO_INDIA_TIMEZONE", "Asia/Kolkata")
    if tenant_id == "asia-id":
        return os.getenv("MONGO_ASIA_ID_TIMEZONE", "Asia/Jakarta")
    if tenant_id == "asia-ph":
        return os.getenv("MONGO_ASIA_PH_TIMEZONE", "Asia/Manila")
    raise ValueError(
        f"No business timezone configured for ASIA tenant {tenant_id!r}"
    )


def _system_sources(mongo_client: MongoClient) -> dict[Region, object]:
    return {
        Region.VN: MongoVnSource(
            mongo_client,
            os.getenv("MONGO_VN_DATABASE", "paymenthub_vn"),
            os.getenv("MONGO_VN_COLLECTION", "payment_transactions"),
            mapping=VNMapping(
                instrument_by_method={"CARD": Instrument.CARD},
                card_link_methods=frozenset({"LINKCARD"}),
            ),
            business_timezone=ZoneInfo(_business_timezone_name(Region.VN, None)),
        ),
        Region.ASIA: MongoAsiaSource(
            mongo_client,
            {
                "asia-id": os.getenv(
                    "MONGO_ASIA_ID_DATABASE", "paymenthub_asia_id"
                ),
                "asia-ph": os.getenv(
                    "MONGO_ASIA_PH_DATABASE", "paymenthub_asia_ph"
                ),
            },
            os.getenv("MONGO_ASIA_COLLECTION", "payment_transactions"),
            mapping=_asia_mapping(),
            business_timezones={
                "asia-id": ZoneInfo(
                    _business_timezone_name(Region.ASIA, "asia-id")
                ),
                "asia-ph": ZoneInfo(
                    _business_timezone_name(Region.ASIA, "asia-ph")
                ),
            },
        ),
        Region.INDIA: MongoIndiaSource(
            mongo_client,
            os.getenv("MONGO_INDIA_DATABASE", "paymenthub_india"),
            os.getenv("MONGO_INDIA_COLLECTION", "payment_transactions"),
            business_timezone=ZoneInfo(
                _business_timezone_name(Region.INDIA, None)
            ),
        ),
    }


def _run_reconciliation_job(parameters: dict, metrics_connection) -> None:
    """Run one reconciliation in a disposable child process."""
    parameters = dict(parameters)
    statement_path = parameters.pop("statement_path")
    region = parameters["region"]
    tenant_id = parameters["tenant_id"]
    mongo_client = MongoClient(
        os.getenv("MONGO_URI", "mongodb://localhost:27017"),
        tz_aware=True,
        serverSelectionTimeoutMS=5_000,
    )
    try:
        sources = _system_sources(mongo_client)
        with connect(_database_url()) as connection:
            RunMatching(
                sources[region],
                CsvFixtureParser(),
                PostgreReconRepository(
                    connection,
                    business_timezone=_business_timezone_name(region, tenant_id),
                ),
                _ProcessTelemetry(metrics_connection),
            ).execute(
                **parameters,
                statement_content=statement_path.read_bytes(),
            )
    finally:
        metrics_connection.close()
        mongo_client.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    postgres_pool = ConnectionPool(
        conninfo=_database_url(),
        min_size=1,
        max_size=int(os.getenv("DATABASE_POOL_SIZE", "5")),
        open=True, #open connection instantly
    )
    process_context = multiprocessing.get_context("spawn")
    executor = ThreadPoolExecutor(
        max_workers=int(os.getenv("RECON_WORKER_COUNT", "1")),
        thread_name_prefix="reconciliation",
    )

    def submit_reconciliation(**parameters) -> UUID:
        statement_path = parameters.pop("statement_path")
        region = parameters["region"]
        tenant_id = parameters["tenant_id"]
        try:
            with postgres_pool.connection() as connection:
                repository = PostgreReconRepository(
                    connection,
                    business_timezone=_business_timezone_name(region, tenant_id),
                )
                submission = repository.enqueue_run(
                    **parameters,
                    statement_checksum=_file_checksum(statement_path),
                )

            if not submission.should_start:
                statement_path.unlink(missing_ok=True)
                return submission.run_id

            job_parameters = dict(parameters, run_id=submission.run_id)

            def process() -> None:
                metrics_reader, metrics_writer = process_context.Pipe(duplex=False)
                worker_parameters = {
                    **job_parameters,
                    "statement_path": statement_path,
                }
                try:
                    # The thread keeps the queue serial. Heavy work runs in a
                    # fresh process so the OS reclaims all memory after the run.
                    worker = process_context.Process(
                        target=_run_reconciliation_job,
                        args=(worker_parameters, metrics_writer),
                        name=f"reconciliation-{submission.run_id}",
                    )
                    worker.start()
                    metrics_writer.close()
                    worker.join()
                    # Metric payloads are tiny. Replay them in the API process
                    # because Prometheus state is not shared between processes.
                    while metrics_reader.poll():
                        try:
                            method, values = metrics_reader.recv()
                        except EOFError:
                            break
                        getattr(reconciliation_metrics, method)(**values)
                    if worker.exitcode != 0:
                        with postgres_pool.connection() as connection:
                            PostgreReconRepository(
                                connection,
                                business_timezone=_business_timezone_name(
                                    region, tenant_id
                                ),
                            ).fail_abandoned_run(
                                submission.run_id,
                                f"worker process exited with code {worker.exitcode}",
                            )
                        logger.error(
                            "Reconciliation worker process failed",
                            extra={
                                "run_id": str(submission.run_id),
                                "exit_code": worker.exitcode,
                            },
                        )
                except Exception:
                    logger.exception(
                        "Background reconciliation failed",
                        extra={"run_id": str(submission.run_id)},
                    )
                finally:
                    metrics_writer.close()
                    metrics_reader.close()
                    statement_path.unlink(missing_ok=True)

            executor.submit(process)
            return submission.run_id
        except Exception:
            statement_path.unlink(missing_ok=True)
            raise

    app.state.query_reconciliation = QueryReconciliation(
        PooledReconQuery(postgres_pool), export_report,
    )
    # The API uses the same schedule configuration as the scheduler process.
    app.state.freshness = FreshnessService(
        load_schedules(os.getenv("RECON_SCHEDULES_JSON", "[]")),
        PooledReconQuery(postgres_pool),
    )
    app.state.delete_reconciliation_run = PooledReconRunDeleter(postgres_pool)
    app.state.submit_reconciliation = submit_reconciliation
    try:
        yield
    finally:
        executor.shutdown(wait=True, cancel_futures=False)
        postgres_pool.close()
