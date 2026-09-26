from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid4

import pytest
from fastapi.testclient import TestClient

from hotieler.container import build_container
from hotieler.infrastructure.clock import DeterministicIdGenerator, FixedClock
from hotieler.main import create_app


def _new_client() -> TestClient:
    clock = FixedClock(datetime(2026, 1, 10, 12, 0, tzinfo=UTC))
    container = build_container(
        clock=clock,
        ids=DeterministicIdGenerator(NAMESPACE_URL, prefix=str(uuid4())),
    )
    return TestClient(create_app(container))


def _onboard(client: TestClient, *, units: int = 1) -> tuple[str, str]:
    owner_response = client.post(
        "/api/v1/owners",
        json={"name": "Aster Stays", "contact_email": "owner@aster.example"},
    )
    assert owner_response.status_code == 201, owner_response.text
    owner_id = owner_response.json()["id"]

    property_response = client.post(
        f"/api/v1/owners/{owner_id}/properties",
        json={
            "name": "Aster Residency",
            "city": "Bengaluru",
            "locality": "Indiranagar",
            "address": "100 Feet Road",
            "star_rating": "4.5",
            "amenities": ["WiFi", "Parking"],
            "room_types": [
                {
                    "name": "Deluxe",
                    "total_units": units,
                    "guests_per_unit": 2,
                    "nightly_rate": {"amount": "2500.00", "currency": "INR"},
                    "amenities": ["Breakfast", "Air Conditioning"],
                }
            ],
        },
    )
    assert property_response.status_code == 201, property_response.text
    body = property_response.json()
    return body["id"], body["room_types"][0]["id"]


def _booking_payload(property_id: str, room_type_id: str) -> dict[str, Any]:
    return {
        "property_id": property_id,
        "room_type_id": room_type_id,
        "check_in": "2026-01-15",
        "check_out": "2026-01-17",
        "guest_count": 2,
    }


def _search(client: TestClient) -> list[dict[str, Any]]:
    response = client.get(
        "/api/v1/properties/search",
        params={
            "city": "  bengaluru  ",
            "locality": "INDIRANAGAR",
            "check_in": "2026-01-15",
            "check_out": "2026-01-17",
            "guest_count": 2,
            "min_price": "2000.00",
            "max_price": "3000.00",
            "min_star_rating": "4",
            "amenities": ["wifi", "breakfast"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_health_and_openapi_expose_the_supported_contract() -> None:
    client = _new_client()

    assert client.get("/health").json() == {"status": "healthy"}
    schema = client.get("/openapi.json").json()
    expected_paths = {
        "/api/v1/owners",
        "/api/v1/owners/{owner_id}/properties",
        "/api/v1/properties/search",
        "/api/v1/bookings",
        "/api/v1/bookings/{booking_id}",
        "/api/v1/bookings/{booking_id}/payments",
        "/api/v1/bookings/{booking_id}/cancel",
    }
    assert expected_paths <= set(schema["paths"])


def test_fresh_application_starts_with_an_empty_catalogue() -> None:
    client = _new_client()

    assert _search(client) == []


def test_approved_payment_replay_cancellation_and_inventory_release() -> None:
    client = _new_client()
    property_id, room_type_id = _onboard(client)

    quotes = _search(client)
    assert len(quotes) == 1
    assert quotes[0]["required_units"] == 1
    assert quotes[0]["available_units"] == 1
    assert quotes[0]["total_price"] == {"amount": "5000.00", "currency": "INR"}

    created = client.post("/api/v1/bookings", json=_booking_payload(property_id, room_type_id))
    assert created.status_code == 201, created.text
    booking = created.json()
    assert booking["status"] == "PENDING_PAYMENT"
    assert _search(client) == []

    payment_payload = {"method": "CARD", "mock_outcome": "APPROVED"}
    paid = client.post(
        f"/api/v1/bookings/{booking['id']}/payments",
        headers={"Idempotency-Key": "approved-flow-1"},
        json=payment_payload,
    )
    assert paid.status_code == 200, paid.text
    paid_body = paid.json()
    assert paid_body["booking"]["status"] == "CONFIRMED"
    assert paid_body["payment"]["status"] == "APPROVED"
    assert paid_body["replayed"] is False

    replay = client.post(
        f"/api/v1/bookings/{booking['id']}/payments",
        headers={"Idempotency-Key": "approved-flow-1"},
        json=payment_payload,
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["payment"]["id"] == paid_body["payment"]["id"]

    fetched = client.get(f"/api/v1/bookings/{booking['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "CONFIRMED"

    cancelled = client.post(f"/api/v1/bookings/{booking['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    cancelled_body = cancelled.json()
    assert cancelled_body["status"] == "CANCELLED"
    assert cancelled_body["cancellation"] == {
        "cancelled_at": "2026-01-10T12:00:00Z",
        "refund_amount": {"amount": "5000.00", "currency": "INR"},
        "refund_percentage": "100",
        "refund_status": "CALCULATED",
    }

    repeated_cancel = client.post(f"/api/v1/bookings/{booking['id']}/cancel")
    assert repeated_cancel.status_code == 200
    assert repeated_cancel.json() == cancelled_body
    assert len(_search(client)) == 1


def test_rejected_payment_returns_200_and_immediately_releases_inventory() -> None:
    client = _new_client()
    property_id, room_type_id = _onboard(client)
    booking = client.post(
        "/api/v1/bookings", json=_booking_payload(property_id, room_type_id)
    ).json()

    rejected = client.post(
        f"/api/v1/bookings/{booking['id']}/payments",
        headers={"Idempotency-Key": "rejected-flow-1"},
        json={"method": "UPI", "mock_outcome": "REJECTED"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["payment"]["status"] == "REJECTED"
    assert rejected.json()["booking"]["status"] == "PAYMENT_FAILED"
    assert len(_search(client)) == 1


@pytest.mark.parametrize("method", ["CARD", "UPI", "WALLET"])
def test_every_payment_method_is_registered(method: str) -> None:
    client = _new_client()
    property_id, room_type_id = _onboard(client)
    booking = client.post(
        "/api/v1/bookings", json=_booking_payload(property_id, room_type_id)
    ).json()

    response = client.post(
        f"/api/v1/bookings/{booking['id']}/payments",
        headers={"Idempotency-Key": f"method-{method}"},
        json={"method": method, "mock_outcome": "APPROVED"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["payment"]["method"] == method


def test_conflicts_and_validation_use_the_stable_error_envelope() -> None:
    client = _new_client()
    property_id, room_type_id = _onboard(client)
    first = client.post("/api/v1/bookings", json=_booking_payload(property_id, room_type_id))
    assert first.status_code == 201

    exhausted = client.post("/api/v1/bookings", json=_booking_payload(property_id, room_type_id))
    assert exhausted.status_code == 409
    assert exhausted.json()["error"]["code"] == "ROOM_INVENTORY_UNAVAILABLE"

    booking_id = first.json()["id"]
    initial_payment = client.post(
        f"/api/v1/bookings/{booking_id}/payments",
        headers={"Idempotency-Key": "same-key"},
        json={"method": "CARD", "mock_outcome": "APPROVED"},
    )
    assert initial_payment.status_code == 200
    changed_fingerprint = client.post(
        f"/api/v1/bookings/{booking_id}/payments",
        headers={"Idempotency-Key": "same-key"},
        json={"method": "WALLET", "mock_outcome": "APPROVED"},
    )
    assert changed_fingerprint.status_code == 409
    assert changed_fingerprint.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"

    missing = client.get(f"/api/v1/bookings/{uuid4()}")
    assert missing.status_code == 404
    assert set(missing.json()["error"]) == {"code", "message", "details"}

    missing_header = client.post(
        f"/api/v1/bookings/{booking_id}/payments",
        json={"method": "CARD", "mock_outcome": "APPROVED"},
    )
    assert missing_header.status_code == 422
    assert missing_header.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"
