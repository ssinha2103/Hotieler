from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid4

from fastapi.testclient import TestClient
from httpx2 import Response

from hotieler.container import build_container
from hotieler.infrastructure.clock import DeterministicIdGenerator, FixedClock
from hotieler.main import create_app


def _new_client(
    now: datetime = datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
) -> tuple[TestClient, FixedClock]:
    clock = FixedClock(now)
    container = build_container(
        clock=clock,
        ids=DeterministicIdGenerator(NAMESPACE_URL, prefix=str(uuid4())),
    )
    return TestClient(create_app(container)), clock


def _create_owner(client: TestClient) -> str:
    response = client.post(
        "/api/v1/owners",
        json={"name": "Cedar Hospitality", "contact_email": "owner@cedar.example"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _property_payload(
    *,
    name: str = "Cedar House",
    city: str = "Bengaluru",
    locality: str = "Indiranagar",
    total_units: int = 1,
    guests_per_unit: int = 2,
    nightly_rate: str = "1200.00",
) -> dict[str, Any]:
    return {
        "name": name,
        "city": city,
        "locality": locality,
        "address": f"1 {name} Road",
        "star_rating": "4.5",
        "amenities": ["Parking"],
        "room_types": [
            {
                "name": "Standard",
                "total_units": total_units,
                "guests_per_unit": guests_per_unit,
                "nightly_rate": {"amount": nightly_rate, "currency": "INR"},
                "amenities": ["WiFi"],
            }
        ],
    }


def _create_property(
    client: TestClient,
    owner_id: str,
    **overrides: Any,
) -> tuple[str, str]:
    response = client.post(
        f"/api/v1/owners/{owner_id}/properties",
        json=_property_payload(**overrides),
    )
    assert response.status_code == 201, response.text
    property_body = response.json()
    return str(property_body["id"]), str(property_body["room_types"][0]["id"])


def _booking_payload(
    property_id: str,
    room_type_id: str,
    *,
    check_in: str = "2026-01-15",
    check_out: str = "2026-01-17",
    guest_count: int = 2,
) -> dict[str, Any]:
    return {
        "property_id": property_id,
        "room_type_id": room_type_id,
        "check_in": check_in,
        "check_out": check_out,
        "guest_count": guest_count,
    }


def _create_booking(
    client: TestClient,
    property_id: str,
    room_type_id: str,
    **overrides: Any,
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/bookings",
        json=_booking_payload(property_id, room_type_id, **overrides),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _pay(
    client: TestClient,
    booking_id: str,
    *,
    key: str,
    method: str = "CARD",
    outcome: str = "APPROVED",
) -> Response:
    return client.post(
        f"/api/v1/bookings/{booking_id}/payments",
        headers={"Idempotency-Key": key},
        json={"method": method, "mock_outcome": outcome},
    )


def _search(
    client: TestClient,
    *,
    check_in: str = "2026-01-15",
    check_out: str = "2026-01-17",
    guest_count: int = 2,
    amenities: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    params = [
        ("city", "  bEnGaLuRu  "),
        ("check_in", check_in),
        ("check_out", check_out),
        ("guest_count", str(guest_count)),
    ]
    params.extend(("amenities", amenity) for amenity in amenities)
    response = client.get("/api/v1/properties/search", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _assert_error(response: Response, status_code: int, code: str) -> dict[str, Any]:
    assert response.status_code == status_code, response.text
    error = response.json()["error"]
    assert set(error) == {"code", "message", "details"}
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["details"], dict)
    return error


def test_chain_search_multi_unit_hold_adjacency_and_pending_cancellation_flow() -> None:
    client, _ = _new_client()
    owner_id = _create_owner(client)
    birch_property_id, _ = _create_property(
        client,
        owner_id,
        name="Birch House",
        total_units=2,
    )
    aster_property_id, aster_room_id = _create_property(
        client,
        owner_id,
        name="Aster House",
        total_units=2,
    )

    quotes = _search(client, guest_count=3, amenities=("PARKING", "wifi"))
    assert [quote["property_name"] for quote in quotes] == ["Aster House", "Birch House"]
    assert all(quote["required_units"] == 2 for quote in quotes)
    assert all(quote["available_units"] == 2 for quote in quotes)
    assert all(quote["total_price"] == {"amount": "4800.00", "currency": "INR"} for quote in quotes)

    booking = _create_booking(
        client,
        aster_property_id,
        aster_room_id,
        guest_count=3,
    )
    assert booking["required_units"] == 2
    assert booking["total_price"] == {"amount": "4800.00", "currency": "INR"}

    overlapping = _search(
        client,
        check_in="2026-01-16",
        check_out="2026-01-18",
        guest_count=3,
    )
    assert [quote["property_id"] for quote in overlapping] == [birch_property_id]

    adjacent = _search(
        client,
        check_in="2026-01-17",
        check_out="2026-01-19",
        guest_count=3,
    )
    assert [quote["property_name"] for quote in adjacent] == ["Aster House", "Birch House"]

    cancelled = client.post(f"/api/v1/bookings/{booking['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["cancellation"] == {
        "cancelled_at": "2026-01-10T12:00:00Z",
        "refund_amount": {"amount": "0.00", "currency": "INR"},
        "refund_percentage": "0",
        "refund_status": "NOT_REQUIRED",
    }
    assert [quote["property_name"] for quote in _search(client, guest_count=3)] == [
        "Aster House",
        "Birch House",
    ]


def test_payment_replay_after_cancellation_returns_the_original_payment_snapshot() -> None:
    client, _ = _new_client()
    owner_id = _create_owner(client)
    property_id, room_type_id = _create_property(client, owner_id)
    booking = _create_booking(client, property_id, room_type_id)

    first_payment = _pay(client, booking["id"], key="snapshot-replay")
    assert first_payment.status_code == 200, first_payment.text
    first_body = first_payment.json()
    assert first_body["booking"]["status"] == "CONFIRMED"

    cancelled = client.post(f"/api/v1/bookings/{booking['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"

    replay = _pay(client, booking["id"], key="snapshot-replay")
    assert replay.status_code == 200, replay.text
    replay_body = replay.json()
    assert replay_body["replayed"] is True
    assert replay_body["payment"] == first_body["payment"]
    assert replay_body["booking"]["status"] == "CONFIRMED"
    assert replay_body["booking"]["cancellation"] is None

    current = client.get(f"/api/v1/bookings/{booking['id']}")
    assert current.status_code == 200
    assert current.json()["status"] == "CANCELLED"


def test_rejected_payment_is_terminal_and_released_inventory_can_complete_a_new_flow() -> None:
    client, _ = _new_client()
    owner_id = _create_owner(client)
    property_id, room_type_id = _create_property(client, owner_id)
    failed_booking = _create_booking(client, property_id, room_type_id)

    rejected = _pay(
        client,
        failed_booking["id"],
        key="terminal-rejection",
        method="UPI",
        outcome="REJECTED",
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["booking"]["status"] == "PAYMENT_FAILED"

    second_payment = _pay(client, failed_booking["id"], key="payment-after-rejection")
    _assert_error(second_payment, 409, "INVALID_BOOKING_TRANSITION")

    failed_cancel = client.post(f"/api/v1/bookings/{failed_booking['id']}/cancel")
    _assert_error(failed_cancel, 409, "CANCELLATION_NOT_ALLOWED")

    replacement = _create_booking(client, property_id, room_type_id)
    approved = _pay(
        client,
        replacement["id"],
        key="replacement-approval",
        method="WALLET",
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["booking"]["status"] == "CONFIRMED"


def test_onboarding_and_booking_resource_errors_preserve_context() -> None:
    client, _ = _new_client()
    owner_id = _create_owner(client)
    first_property_id, _ = _create_property(client, owner_id, name="First House")
    _, second_room_id = _create_property(client, owner_id, name="Second House")

    missing_owner_id = uuid4()
    missing_owner = client.post(
        f"/api/v1/owners/{missing_owner_id}/properties",
        json=_property_payload(),
    )
    missing_owner_error = _assert_error(missing_owner, 404, "RESOURCE_NOT_FOUND")
    assert missing_owner_error["details"] == {
        "resource": "Owner",
        "id": str(missing_owner_id),
    }

    mismatched_room = client.post(
        "/api/v1/bookings",
        json=_booking_payload(first_property_id, second_room_id),
    )
    mismatch_error = _assert_error(mismatched_room, 409, "PROPERTY_ROOM_MISMATCH")
    assert mismatch_error["details"] == {
        "property_id": first_property_id,
        "room_type_id": second_room_id,
    }

    missing_property_id = uuid4()
    missing_property = client.post(
        "/api/v1/bookings",
        json=_booking_payload(str(missing_property_id), str(uuid4())),
    )
    missing_property_error = _assert_error(missing_property, 404, "RESOURCE_NOT_FOUND")
    assert missing_property_error["details"] == {
        "resource": "Property",
        "id": str(missing_property_id),
    }

    missing_room_id = uuid4()
    missing_room = client.post(
        "/api/v1/bookings",
        json=_booking_payload(first_property_id, str(missing_room_id)),
    )
    missing_room_error = _assert_error(missing_room, 404, "RESOURCE_NOT_FOUND")
    assert missing_room_error["details"] == {
        "resource": "Room type",
        "id": str(missing_room_id),
    }


def test_transport_and_domain_validation_failures_use_the_same_error_shape() -> None:
    client, _ = _new_client()

    non_finite_guest_count = client.post(
        "/api/v1/bookings",
        content=(
            '{"property_id":"11111111-1111-4111-8111-111111111111",'
            '"room_type_id":"22222222-2222-4222-8222-222222222222",'
            '"check_in":"2026-01-15","check_out":"2026-01-17",'
            '"guest_count":1e400}'
        ),
        headers={"Content-Type": "application/json"},
    )
    non_finite_error = _assert_error(
        non_finite_guest_count,
        422,
        "REQUEST_VALIDATION_ERROR",
    )
    assert non_finite_error["details"]["errors"]
    assert all(
        set(error) == {"type", "loc", "msg"} for error in non_finite_error["details"]["errors"]
    )

    invalid_email = client.post(
        "/api/v1/owners",
        json={"name": "Invalid Owner", "contact_email": "not-an-email"},
    )
    _assert_error(invalid_email, 422, "REQUEST_VALIDATION_ERROR")

    owner_id = _create_owner(client)
    non_inr = client.post(
        f"/api/v1/owners/{owner_id}/properties",
        json={
            **_property_payload(),
            "room_types": [
                {
                    **_property_payload()["room_types"][0],
                    "nightly_rate": {"amount": "1200.00", "currency": "USD"},
                }
            ],
        },
    )
    _assert_error(non_inr, 422, "REQUEST_VALIDATION_ERROR")

    forbidden_extra_field = client.post(
        "/api/v1/owners",
        json={
            "name": "Extra Field Owner",
            "contact_email": "owner@example.com",
            "unexpected": True,
        },
    )
    request_error = _assert_error(
        forbidden_extra_field,
        422,
        "REQUEST_VALIDATION_ERROR",
    )
    assert request_error["details"]["errors"][0]["type"] == "extra_forbidden"

    reversed_price_range = client.get(
        "/api/v1/properties/search",
        params={
            "city": "Bengaluru",
            "check_in": "2026-01-15",
            "check_out": "2026-01-17",
            "guest_count": 2,
            "min_price": "2000.00",
            "max_price": "1000.00",
        },
    )
    _assert_error(reversed_price_range, 422, "DOMAIN_VALIDATION_ERROR")

    for price_parameter in ("min_price", "max_price"):
        oversized_price = client.get(
            "/api/v1/properties/search",
            params={
                "city": "Bengaluru",
                "check_in": "2026-01-15",
                "check_out": "2026-01-17",
                "guest_count": 2,
                price_parameter: "1e28",
            },
        )
        oversized_error = _assert_error(
            oversized_price,
            422,
            "REQUEST_VALIDATION_ERROR",
        )
        assert oversized_error["details"]["errors"][0]["loc"][-1] == price_parameter

    invalid_stay = client.get(
        "/api/v1/properties/search",
        params={
            "city": "Bengaluru",
            "check_in": "2026-01-15",
            "check_out": "2026-01-15",
            "guest_count": 2,
        },
    )
    _assert_error(invalid_stay, 422, "DOMAIN_VALIDATION_ERROR")

    owner_id = _create_owner(client)
    property_id, room_type_id = _create_property(client, owner_id)
    booking = _create_booking(client, property_id, room_type_id)
    blank_key = _pay(client, booking["id"], key="   ")
    _assert_error(blank_key, 422, "DOMAIN_VALIDATION_ERROR")

    invalid_payment_enum = _pay(
        client,
        booking["id"],
        key="invalid-method",
        method="CASH",
    )
    _assert_error(invalid_payment_enum, 422, "REQUEST_VALIDATION_ERROR")


def test_http_cancellation_supports_partial_zero_and_rejected_refund_boundaries() -> None:
    client, clock = _new_client()
    owner_id = _create_owner(client)
    property_id, room_type_id = _create_property(client, owner_id)

    partial_booking = _create_booking(
        client,
        property_id,
        room_type_id,
        check_in="2026-01-11",
        check_out="2026-01-13",
    )
    partial_payment = _pay(client, partial_booking["id"], key="partial-refund")
    assert partial_payment.status_code == 200, partial_payment.text
    partial_cancel = client.post(f"/api/v1/bookings/{partial_booking['id']}/cancel")
    assert partial_cancel.status_code == 200, partial_cancel.text
    assert partial_cancel.json()["cancellation"] == {
        "cancelled_at": "2026-01-10T12:00:00Z",
        "refund_amount": {"amount": "1200.00", "currency": "INR"},
        "refund_percentage": "50",
        "refund_status": "CALCULATED",
    }

    zero_booking = _create_booking(
        client,
        property_id,
        room_type_id,
        check_in="2026-01-11",
        check_out="2026-01-12",
    )
    zero_payment = _pay(client, zero_booking["id"], key="zero-refund")
    assert zero_payment.status_code == 200, zero_payment.text
    clock.set(datetime(2026, 1, 11, 12, 0, tzinfo=UTC))
    zero_cancel = client.post(f"/api/v1/bookings/{zero_booking['id']}/cancel")
    assert zero_cancel.status_code == 200, zero_cancel.text
    assert zero_cancel.json()["cancellation"] == {
        "cancelled_at": "2026-01-11T12:00:00Z",
        "refund_amount": {"amount": "0.00", "currency": "INR"},
        "refund_percentage": "0",
        "refund_status": "NOT_REQUIRED",
    }

    past_booking = _create_booking(
        client,
        property_id,
        room_type_id,
        check_in="2026-01-12",
        check_out="2026-01-13",
    )
    past_payment = _pay(client, past_booking["id"], key="past-check-in")
    assert past_payment.status_code == 200, past_payment.text
    clock.set(datetime(2026, 1, 13, 12, 0, tzinfo=UTC))
    rejected_cancel = client.post(f"/api/v1/bookings/{past_booking['id']}/cancel")
    _assert_error(rejected_cancel, 409, "CANCELLATION_NOT_ALLOWED")


def test_idempotency_key_cannot_be_reused_for_another_booking() -> None:
    client, _ = _new_client()
    owner_id = _create_owner(client)
    property_id, room_type_id = _create_property(client, owner_id, total_units=2)
    first_booking = _create_booking(client, property_id, room_type_id)
    second_booking = _create_booking(client, property_id, room_type_id)

    first_payment = _pay(client, first_booking["id"], key="globally-unique-key")
    assert first_payment.status_code == 200, first_payment.text

    conflicting_payment = _pay(client, second_booking["id"], key="globally-unique-key")
    conflict = _assert_error(conflicting_payment, 409, "IDEMPOTENCY_KEY_CONFLICT")
    assert conflict["details"] == {"idempotency_key": "globally-unique-key"}

    second_state = client.get(f"/api/v1/bookings/{second_booking['id']}")
    assert second_state.status_code == 200
    assert second_state.json()["status"] == "PENDING_PAYMENT"
