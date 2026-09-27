"""Stable HTTP error envelopes for application and validation failures."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from hotieler.api.observability import REQUEST_ID_HEADER, request_id_from_state
from hotieler.domain.errors import (
    ConflictError,
    DomainValidationError,
    HotielerError,
    ResourceNotFoundError,
)

logger = logging.getLogger("hotieler.api.errors")


def _envelope(code: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def _route_path(request: Request) -> str:
    route_path = getattr(request.scope.get("route"), "path", None)
    return route_path if isinstance(route_path, str) else request.url.path


def _public_validation_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Return the stable, JSON-safe subset of Pydantic validation errors."""

    return [
        {
            "type": str(error.get("type", "value_error")),
            "loc": [
                part if isinstance(part, (str, int)) else str(part) for part in error.get("loc", ())
            ],
            "msg": str(error.get("msg", "Invalid value.")),
        }
        for error in exc.errors()
    ]


async def hotieler_error_handler(request: Request, exc: HotielerError) -> JSONResponse:
    if isinstance(exc, ResourceNotFoundError):
        status_code = 404
    elif isinstance(exc, ConflictError):
        status_code = 409
    elif isinstance(exc, DomainValidationError):
        status_code = 422
    else:
        status_code = 500
    logger.warning(
        "http_request_rejected",
        extra={
            "request_id": request_id_from_state(request.state),
            "http_method": request.method,
            "http_route": _route_path(request),
            "http_status_code": status_code,
            "error_code": exc.code,
        },
    )
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(_envelope(exc.code, exc.message, exc.details)),
    )


async def request_validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    logger.warning(
        "http_request_rejected",
        extra={
            "request_id": request_id_from_state(request.state),
            "http_method": request.method,
            "http_route": _route_path(request),
            "http_status_code": 422,
            "error_code": "REQUEST_VALIDATION_ERROR",
        },
    )
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder(
            _envelope(
                "REQUEST_VALIDATION_ERROR",
                "The request could not be validated.",
                {"errors": _public_validation_errors(exc)},
            )
        ),
    )


async def unexpected_error_handler(request: Request, _exc: Exception) -> JSONResponse:
    """Hide internal exception details behind the stable public error contract."""

    request_id = request_id_from_state(request.state)
    headers = {REQUEST_ID_HEADER: request_id} if request_id is not None else None
    return JSONResponse(
        status_code=500,
        content=_envelope(
            "INTERNAL_SERVER_ERROR",
            "An unexpected internal error occurred.",
            {},
        ),
        headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HotielerError, hotieler_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unexpected_error_handler)
