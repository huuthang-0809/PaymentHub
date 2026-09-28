"""ASGI entry point and HTTP application factory."""
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from bootstrap.bootstrap import lifespan
from reconciliation.adapters.http import router


def create_app() -> FastAPI:
    app = FastAPI(title="PaymentHub API", lifespan=lifespan)
    app.include_router(router)
    app.mount("/metrics", make_asgi_app()) # 

    @app.get("/health")
    def health_check():
        return {"status": "ok", "service": "paymenthub"}

    return app


app = create_app()
