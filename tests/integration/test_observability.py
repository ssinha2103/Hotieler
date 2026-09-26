"""HTTP observability and unexpected-failure contract tests."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from hotieler.api.observability import JsonLogFormatter
from hotieler.container import build_container
from hotieler.infrastructure.clock import FixedClock
from hotieler.main import create_app


class _RecordHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextmanager
def _capture(logger_name: str) -> Iterator[list[logging.LogRecord]]:
    logger = logging.getLogger(logger_name)
    handler = _RecordHandler()
    logger.addHandler(handler)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)


def test_every_response_has_request_id_and_safe_client_id_is_reused() -> None:
    client = TestClient(create_app())

    with _capture("hotieler.http") as records:
        response = client.get("/health", headers={"X-Request-ID": "client-request_001"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "client-request_001"
    completed = next(
        record for record in records if record.getMessage() == "http_request_completed"
    )
    assert completed.request_id == "client-request_001"  # type: ignore[attr-defined]
    assert completed.http_method == "GET"  # type: ignore[attr-defined]
    assert completed.http_route == "/health"  # type: ignore[attr-defined]
    assert completed.http_status_code == 200  # type: ignore[attr-defined]
    assert completed.duration_ms >= 0  # type: ignore[attr-defined]


def test_invalid_or_oversized_request_id_is_replaced() -> None:
    client = TestClient(create_app())

    invalid = client.get("/health", headers={"X-Request-ID": "contains spaces"})
    oversized = client.get("/health", headers={"X-Request-ID": "a" * 65})

    assert UUID(invalid.headers["X-Request-ID"])
    assert UUID(oversized.headers["X-Request-ID"])
    assert invalid.headers["X-Request-ID"] != "contains spaces"
    assert oversized.headers["X-Request-ID"] != "a" * 65


def test_request_log_is_structured_and_never_contains_body_or_sensitive_headers() -> None:
    client = TestClient(create_app())
    secret_key = "never-log-this-idempotency-key"

    with _capture("hotieler.http") as records:
        response = client.post(
            "/api/v1/bookings/11111111-1111-4111-8111-111111111111/payments",
            headers={"Idempotency-Key": secret_key},
            json={"method": "CARD", "mock_outcome": "APPROVED"},
        )

    assert response.status_code == 404
    completed = next(
        record for record in records if record.getMessage() == "http_request_completed"
    )
    payload = json.loads(JsonLogFormatter().format(completed))
    assert payload["http_route"] == "/api/v1/bookings/{booking_id}/payments"
    assert payload["http_status_code"] == 404
    rendered = json.dumps(payload)
    assert secret_key not in rendered
    assert "mock_outcome" not in rendered
    assert "APPROVED" not in rendered


def test_unmatched_route_does_not_log_the_caller_supplied_path() -> None:
    client = TestClient(create_app())
    caller_path = "/not-found/private-caller-value"

    with _capture("hotieler.http") as records:
        response = client.get(caller_path)

    assert response.status_code == 404
    completed = next(
        record for record in records if record.getMessage() == "http_request_completed"
    )
    payload = json.loads(JsonLogFormatter().format(completed))
    assert payload["http_route"] == "<unmatched>"
    assert "private-caller-value" not in json.dumps(payload)


def test_unhandled_exception_returns_safe_envelope_and_is_correlated() -> None:
    app = create_app()

    @app.get("/test-only-explosion")
    def explode() -> None:
        raise RuntimeError("internal-sensitive-message")

    client = TestClient(app, raise_server_exceptions=False)
    with _capture("hotieler.http") as records:
        response = client.get(
            "/test-only-explosion",
            headers={"X-Request-ID": "failure-correlation-1"},
        )

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "failure-correlation-1"
    assert response.json() == {
        "error": {
            "code": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected internal error occurred.",
            "details": {},
        }
    }
    assert "internal-sensitive-message" not in response.text
    failed = next(record for record in records if record.getMessage() == "http_request_failed")
    assert failed.request_id == "failure-correlation-1"  # type: ignore[attr-defined]
    assert failed.http_status_code == 500  # type: ignore[attr-defined]
    assert failed.exc_info is not None


def test_business_lifecycle_events_are_safe_and_structured() -> None:
    idempotency_key = "never-emit-business-secret"
    contact_email = "private.owner@aster.example"
    container = build_container(clock=FixedClock(datetime(2030, 1, 1, 10, tzinfo=UTC)))

    with _capture("hotieler.application") as records:
        client = TestClient(create_app(container))
        owner_response = client.post(
            "/api/v1/owners",
            json={"name": "Aster Stays", "contact_email": contact_email},
        )
        assert owner_response.status_code == 201
        owner_id = owner_response.json()["id"]
        property_response = client.post(
            f"/api/v1/owners/{owner_id}/properties",
            json={
                "name": "Aster Residency",
                "city": "Bengaluru",
                "locality": "Indiranagar",
                "address": "100 Feet Road",
                "star_rating": "4.5",
                "amenities": ["WiFi"],
                "room_types": [
                    {
                        "name": "Deluxe",
                        "total_units": 1,
                        "guests_per_unit": 2,
                        "nightly_rate": {"amount": "2500.00", "currency": "INR"},
                        "amenities": ["Breakfast"],
                    }
                ],
            },
        )
        assert property_response.status_code == 201
        property_body = property_response.json()
        booking_response = client.post(
            "/api/v1/bookings",
            json={
                "property_id": property_body["id"],
                "room_type_id": property_body["room_types"][0]["id"],
                "check_in": "2030-01-11",
                "check_out": "2030-01-13",
                "guest_count": 2,
            },
        )
        assert booking_response.status_code == 201
        booking_id = booking_response.json()["id"]
        payment_request = {"method": "CARD", "mock_outcome": "APPROVED"}
        processed = client.post(
            f"/api/v1/bookings/{booking_id}/payments",
            headers={"Idempotency-Key": idempotency_key},
            json=payment_request,
        )
        replayed = client.post(
            f"/api/v1/bookings/{booking_id}/payments",
            headers={"Idempotency-Key": idempotency_key},
            json=payment_request,
        )
        cancelled = client.post(f"/api/v1/bookings/{booking_id}/cancel")

    assert processed.status_code == 200
    assert replayed.status_code == 200
    assert cancelled.status_code == 200
    events = {record.getMessage() for record in records}
    assert {
        "owner_created",
        "property_created",
        "booking_inventory_held",
        "payment_processed",
        "payment_replayed",
        "booking_cancelled",
    } <= events

    rendered = "\n".join(JsonLogFormatter().format(record) for record in records)
    assert idempotency_key not in rendered
    assert contact_email not in rendered
