"""FastAPI application entry point."""

from __future__ import annotations

import os
from datetime import date

from fastapi import FastAPI

from hotieler.api.error_handlers import install_error_handlers
from hotieler.api.routes import router
from hotieler.api.schemas import HealthResponse
from hotieler.container import AppContainer, build_container
from hotieler.demo_data import seed_demo_data

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
        "name": "Demo",
        "description": "Inspect the preloaded demo catalogue and guided test data.",
    },
    {
        "name": "Runtime",
        "description": "Check whether the API process is healthy.",
    },
]


def _demo_seed_enabled() -> bool:
    return os.getenv("HOTIELER_SEED_DEMO_DATA", "false").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def create_app(
    container: AppContainer | None = None,
    *,
    seed_demo: bool | None = None,
    demo_as_of: date | None = None,
) -> FastAPI:
    resolved_container = container or build_container()
    should_seed_demo = (
        seed_demo if seed_demo is not None else container is None and _demo_seed_enabled()
    )
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
    app.state.container = resolved_container
    app.state.demo_data = (
        seed_demo_data(resolved_container, as_of=demo_as_of) if should_seed_demo else None
    )
    install_error_handlers(app)

    @app.get("/health", response_model=HealthResponse, tags=["Runtime"])
    def health() -> HealthResponse:
        return HealthResponse(status="healthy")

    app.include_router(router)
    return app


app = create_app()
