"""ASGI entry point and HTTP application factory."""
import os

from fastapi import FastAPI
from prometheus_client import make_asgi_app

from bootstrap.bootstrap import lifespan
from monitoring.adapters.http import install_monitoring_api
from monitoring.adapters.postgres import PostgresTransactionRepository
from monitoring.adapters.postgres_health_query import PostgresHealthQueryRepository
from reconciliation.adapters.data_quality import router as data_quality_router
from reconciliation.adapters.http import router
from reconciliation.adapters.freshness import router as freshness_router


def _database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://paymenthub:paymenthub@localhost:5432/paymenthub",
    ).replace("postgresql+psycopg://", "postgresql://", 1)


def create_app() -> FastAPI:
    app = FastAPI(title="PaymentHub API", lifespan=lifespan)
    app.include_router(router)
    # Register GET /_meta/freshness on the main FastAPI application.
    app.include_router(freshness_router)
    # Register GET /internal/data-quality on the main FastAPI application.
    app.include_router(data_quality_router)
    install_monitoring_api(
        app,
        PostgresTransactionRepository(_database_url()),
        PostgresHealthQueryRepository(_database_url()),
    )
    app.mount("/metrics", make_asgi_app()) # endpoint for prometheus to read metric in memory of api process

    @app.get("/health")
    def health_check():
        return {"status": "ok", "service": "paymenthub"}

    return app


app = create_app()
