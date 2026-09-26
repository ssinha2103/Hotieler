"""FastAPI application entry point."""

from __future__ import annotations

from fastapi import FastAPI

from hotieler.api.error_handlers import install_error_handlers
from hotieler.api.observability import RequestLoggingMiddleware, configure_logging
from hotieler.api.routes import router
from hotieler.api.schemas import HealthResponse
from hotieler.container import AppContainer, build_container

OPENAPI_TAGS = [
    {
        "name": "Owners",
        "description": "Create hotel-owner accounts used to onboard properties.",
    },
    {
        "name": "Properties & Search",
        "description": "Onboard properties and room types, then search available inventory.",
    },
    {
        "name": "Bookings",
        "description": "Reserve inventory, inspect booking state, and cancel bookings.",
    },
    {
        "name": "Payments",
        "description": "Exercise deterministic, idempotent mock-payment outcomes.",
    },
    {
        "name": "Runtime",
        "description": "Check whether the API process is healthy.",
    },
]


def create_app(container: AppContainer | None = None) -> FastAPI:
    configure_logging()
    resolved_container = container or build_container()
    app = FastAPI(
        title="Hotieler Hotel Booking API",
        version="1.0.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        redoc_url="/redoc",
        description=(
            "Backend-only hotel discovery, booking, deterministic mock payment, "
            "and cancellation service. All data is process-local and in memory."
        ),
        openapi_tags=OPENAPI_TAGS,
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.state.container = resolved_container
    install_error_handlers(app)

    @app.get("/health", response_model=HealthResponse, tags=["Runtime"])
    def health() -> HealthResponse:
        return HealthResponse(status="healthy")

    app.include_router(router)
    return app


app = create_app()
