"""Stable HTTP error envelopes for application and validation failures."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from hotieler.domain.errors import (
    ConflictError,
    DomainValidationError,
    HotielerError,
    ResourceNotFoundError,
)


def _envelope(code: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


async def hotieler_error_handler(_request: Request, exc: HotielerError) -> JSONResponse:
    if isinstance(exc, ResourceNotFoundError):
        status_code = 404
    elif isinstance(exc, ConflictError):
        status_code = 409
    elif isinstance(exc, DomainValidationError):
        status_code = 422
    else:
        status_code = 500
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(_envelope(exc.code, exc.message, exc.details)),
    )


async def request_validation_error_handler(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder(
            _envelope(
                "REQUEST_VALIDATION_ERROR",
                "The request could not be validated.",
                {"errors": exc.errors()},
            )
        ),
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HotielerError, hotieler_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)  # type: ignore[arg-type]
