import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from pymongo import MongoClient
from psycopg_pool import ConnectionPool

from canonical.adapters.asia import AsiaMapping, MongoAsiaSource
from canonical.adapters.india import MongoIndiaSource
from canonical.adapters.vn import MongoVnSource, VNMapping
from reconciliation.adapters import CsvFixtureParser, PostgreReconRepository
from reconciliation.api import router as reconciliation_router
from reconciliation.application import MatchingRunOutput, RunMatching
from shared_kernel import Region
from shared_kernel.enums import Instrument


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    mongo_client = MongoClient(
        os.getenv("MONGO_URI", "mongodb://localhost:27017"),
        tz_aware=True,
        serverSelectionTimeoutMS=5_000,
    )
    postgres_pool = ConnectionPool(
        conninfo=_database_url(),
        min_size=1,
        max_size=int(os.getenv("DATABASE_POOL_SIZE", "5")),
        open=True,
    )

    sources = {
        Region.VN: MongoVnSource(
            mongo_client,
            os.getenv("MONGO_VN_DATABASE", "paymenthub_vn"),
            os.getenv("MONGO_VN_COLLECTION", "payment_transactions"),
            mapping=VNMapping(
                instrument_by_method={"CARD": Instrument.CARD},
                card_link_methods=frozenset({"LINKCARD"}),
            ),
            business_timezone=ZoneInfo(
                _business_timezone_name(Region.VN, None)
            ),
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
    statement_parser = CsvFixtureParser()

    def run_reconciliation(
        *,
        statement: Path,
        statement_content: bytes,
        business_date: date,
        region: Region,
        tenant_id: str | None,
        provider: str,
        run_id: UUID | None = None,
    ) -> MatchingRunOutput:
        with postgres_pool.connection() as connection:
            use_case = RunMatching(
                sources[region],
                statement_parser,
                PostgreReconRepository(
                    connection,
                    business_timezone=_business_timezone_name(region, tenant_id),
                ),
            )
            return use_case.execute(
                statement=statement,
                statement_content=statement_content,
                business_date=business_date,
                region=region,
                tenant_id=tenant_id,
                provider=provider,
                run_id=run_id,
            )

    from reconciliation.adapters.query import PostgreReconQuery

    def query_reconciliation(operation, **parameters):
        with postgres_pool.connection() as connection:
            query = PostgreReconQuery(connection)
            return getattr(query, operation)(**parameters)

    app.state.query_reconciliation = query_reconciliation
    app.state.run_reconciliation = run_reconciliation
    try:
        yield
    finally:
        postgres_pool.close()
        mongo_client.close()


app = FastAPI(title="PaymentHub API", lifespan=lifespan)
app.include_router(reconciliation_router)


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "paymenthub"}
