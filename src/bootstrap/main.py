"""ASGI entry point and HTTP application factory."""
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from bootstrap.bootstrap import lifespan
from reconciliation.adapters.data_quality import router as data_quality_router
from reconciliation.adapters.http import router
from reconciliation.adapters.freshness import router as freshness_router


def create_app() -> FastAPI:
    app = FastAPI(title="PaymentHub API", lifespan=lifespan)
    app.include_router(router)
    # Register GET /_meta/freshness on the main FastAPI application.
    app.include_router(freshness_router)
    # Register GET /internal/data-quality on the main FastAPI application.
    app.include_router(data_quality_router)
    app.mount("/metrics", make_asgi_app()) # endpoint for prometheus to read metric in memory of api process

    @app.get("/health")
    def health_check():
        return {"status": "ok", "service": "paymenthub"}

    return app


app = create_app()
