"""Adversarial contract tests for strict HTTP-boundary validation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from hotieler.api.request_limits import MAX_REQUEST_BODY_BYTES
from hotieler.container import build_container
from hotieler.infrastructure.clock import DeterministicIdGenerator, FixedClock
from hotieler.main import create_app


def _client() -> TestClient:
    container = build_container(
        clock=FixedClock(datetime(2026, 1, 10, 12, 0, tzinfo=UTC)),
        ids=DeterministicIdGenerator(NAMESPACE_URL, prefix=str(uuid4())),
    )
    return TestClient(create_app(container))


def _owner_id(client: TestClient) -> str:
    response = client.post(
        "/api/v1/owners",
        json={"name": "Boundary Hotels", "contact_email": "owner@boundary.example"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _property_payload() -> dict[str, Any]:
    return {
        "name": "Boundary House",
        "city": "Bengaluru",
        "locality": "Indiranagar",
        "address": "100 Main Road",
        "star_rating": "4.5",
        "amenities": ["wifi"],
        "room_types": [
            {
                "name": "Deluxe",
                "total_units": 2,
                "guests_per_unit": 2,
                "nightly_rate": {"amount": "2500.00", "currency": "INR"},
                "amenities": ["breakfast"],
            }
        ],
    }


def _assert_error(response: Response, status_code: int, code: str) -> dict[str, Any]:
    assert response.status_code == status_code, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    return body["error"]


@pytest.mark.parametrize("field", ["total_units", "guests_per_unit"])
@pytest.mark.parametrize("invalid_value", [True, 2.0, "2"])
def test_property_room_counts_require_json_integers(field: str, invalid_value: Any) -> None:
    client = _client()
    payload = _property_payload()
    payload["room_types"][0][field] = invalid_value

    response = client.post(
        f"/api/v1/owners/{_owner_id(client)}/properties",
        json=payload,
    )

    error = _assert_error(response, 422, "REQUEST_VALIDATION_ERROR")
    assert error["details"]["errors"][0]["loc"][-1] == field


@pytest.mark.parametrize("invalid_value", [True, 2.0, "2"])
def test_booking_guest_count_requires_a_json_integer(invalid_value: Any) -> None:
    client = _client()

    response = client.post(
        "/api/v1/bookings",
        json={
            "property_id": str(uuid4()),
            "room_type_id": str(uuid4()),
            "check_in": "2026-01-15",
            "check_out": "2026-01-17",
            "guest_count": invalid_value,
        },
    )

    error = _assert_error(response, 422, "REQUEST_VALIDATION_ERROR")
    assert error["details"]["errors"][0]["loc"][-1] == "guest_count"


def test_human_readable_names_reject_control_characters_through_the_api() -> None:
    client = _client()

    owner = client.post(
        "/api/v1/owners",
        json={"name": "Unsafe\u0000Owner", "contact_email": "owner@example.com"},
    )
    _assert_error(owner, 422, "DOMAIN_VALIDATION_ERROR")

    payload = _property_payload()
    payload["room_types"][0]["name"] = "Unsafe\u007fRoom"
    property_response = client.post(
        f"/api/v1/owners/{_owner_id(client)}/properties",
        json=payload,
    )
    _assert_error(property_response, 422, "DOMAIN_VALIDATION_ERROR")


def test_decimal_step_constraints_are_enforced_at_the_http_boundary() -> None:
    client = _client()
    owner_id = _owner_id(client)
    payload = _property_payload()
    payload["star_rating"] = "4.55"
    invalid_property = client.post(
        f"/api/v1/owners/{owner_id}/properties",
        json=payload,
    )
    _assert_error(invalid_property, 422, "REQUEST_VALIDATION_ERROR")

    search_base = {
        "city": "Bengaluru",
        "check_in": "2026-01-15",
        "check_out": "2026-01-17",
        "guest_count": "2",
    }
    for parameter, value in (("min_price", "100.001"), ("min_star_rating", "4.55")):
        response = client.get(
            "/api/v1/properties/search",
            params={**search_base, parameter: value},
        )
        error = _assert_error(response, 422, "REQUEST_VALIDATION_ERROR")
        assert error["details"]["errors"][0]["loc"][-1] == parameter


@pytest.mark.parametrize("field", ["check_in", "check_out"])
@pytest.mark.parametrize("invalid_value", [1768435200, 1768608000.0, True])
def test_booking_dates_require_iso_date_strings(field: str, invalid_value: Any) -> None:
    client = _client()
    payload: dict[str, Any] = {
        "property_id": str(uuid4()),
        "room_type_id": str(uuid4()),
        "check_in": "2026-01-15",
        "check_out": "2026-01-17",
        "guest_count": 2,
    }
    payload[field] = invalid_value

    response = client.post("/api/v1/bookings", json=payload)

    error = _assert_error(response, 422, "REQUEST_VALIDATION_ERROR")
    assert error["details"]["errors"][0]["loc"][-1] == field


@pytest.mark.parametrize("field", ["check_in", "check_out"])
def test_search_dates_reject_numeric_timestamp_strings(field: str) -> None:
    client = _client()
    params = {
        "city": "Bengaluru",
        "check_in": "2026-01-15",
        "check_out": "2026-01-17",
        "guest_count": "2",
    }
    params[field] = "1768435200"

    response = client.get("/api/v1/properties/search", params=params)

    error = _assert_error(response, 422, "REQUEST_VALIDATION_ERROR")
    assert error["details"]["errors"][0]["loc"][-1] == field


def test_amenity_labels_are_trimmed_for_property_and_room_creation() -> None:
    client = _client()
    payload = _property_payload()
    payload["amenities"] = ["  WiFi  "]
    payload["room_types"][0]["amenities"] = ["  Breakfast  "]

    response = client.post(
        f"/api/v1/owners/{_owner_id(client)}/properties",
        json=payload,
    )

    assert response.status_code == 201, response.text
    assert response.json()["amenities"] == ["wifi"]
    assert response.json()["room_types"][0]["amenities"] == ["breakfast"]


@pytest.mark.parametrize("invalid_label", ["   ", "a" * 121])
@pytest.mark.parametrize("target", ["property", "room"])
def test_property_and_room_amenity_labels_are_bounded(
    target: str,
    invalid_label: str,
) -> None:
    client = _client()
    payload = _property_payload()
    if target == "property":
        payload["amenities"] = [invalid_label]
    else:
        payload["room_types"][0]["amenities"] = [invalid_label]

    response = client.post(
        f"/api/v1/owners/{_owner_id(client)}/properties",
        json=payload,
    )

    _assert_error(response, 422, "REQUEST_VALIDATION_ERROR")


@pytest.mark.parametrize("target", ["property", "room"])
def test_property_and_room_amenity_lists_are_bounded(target: str) -> None:
    client = _client()
    payload = _property_payload()
    if target == "property":
        payload["amenities"] = [f"amenity-{index}" for index in range(101)]
    else:
        payload["room_types"][0]["amenities"] = [f"amenity-{index}" for index in range(101)]

    response = client.post(
        f"/api/v1/owners/{_owner_id(client)}/properties",
        json=payload,
    )

    _assert_error(response, 422, "REQUEST_VALIDATION_ERROR")


def test_search_amenities_are_trimmed_and_bounded() -> None:
    client = _client()
    owner_id = _owner_id(client)
    created = client.post(
        f"/api/v1/owners/{owner_id}/properties",
        json=_property_payload(),
    )
    assert created.status_code == 201, created.text
    base_params = [
        ("city", "Bengaluru"),
        ("check_in", "2026-01-15"),
        ("check_out", "2026-01-17"),
        ("guest_count", "2"),
    ]

    accepted = client.get(
        "/api/v1/properties/search",
        params=[*base_params, ("amenities", "  WiFi  ")],
    )
    assert accepted.status_code == 200, accepted.text
    assert len(accepted.json()) == 1

    for invalid_amenities in (["   "], ["a" * 121], ["wifi"] * 101):
        rejected = client.get(
            "/api/v1/properties/search",
            params=[*base_params, *(("amenities", value) for value in invalid_amenities)],
        )
        _assert_error(rejected, 422, "REQUEST_VALIDATION_ERROR")


def test_transport_http_errors_use_the_stable_envelope_and_preserve_headers() -> None:
    client = _client()

    invalid_utf8 = client.post(
        "/api/v1/owners",
        content=b'{"name":"\xff"}',
        headers={"Content-Type": "application/json"},
    )
    _assert_error(invalid_utf8, 400, "BAD_REQUEST")

    malformed_json = client.post(
        "/api/v1/owners",
        content=b'{"name":',
        headers={"Content-Type": "application/json"},
    )
    _assert_error(malformed_json, 400, "BAD_REQUEST")

    missing_route = client.get("/api/v1/does-not-exist")
    _assert_error(missing_route, 404, "ROUTE_NOT_FOUND")

    wrong_method = client.get("/api/v1/owners")
    _assert_error(wrong_method, 405, "METHOD_NOT_ALLOWED")
    assert wrong_method.headers["allow"] == "POST"


def test_request_body_limit_rejects_an_oversized_http_payload() -> None:
    client = _client()
    oversized_chunk = b"x" * (MAX_REQUEST_BODY_BYTES + 1)

    declared = client.post(
        "/api/v1/owners",
        content=oversized_chunk,
        headers={"Content-Type": "application/json"},
    )
    declared_error = _assert_error(declared, 413, "PAYLOAD_TOO_LARGE")
    assert declared_error["details"] == {"max_bytes": MAX_REQUEST_BODY_BYTES}
    assert "x-request-id" in declared.headers

    streamed = client.post(
        "/api/v1/owners",
        content=iter((b"{", oversized_chunk)),
        headers={"Content-Type": "application/json"},
    )
    streamed_error = _assert_error(streamed, 413, "PAYLOAD_TOO_LARGE")
    assert streamed_error["details"] == {"max_bytes": MAX_REQUEST_BODY_BYTES}
    assert "x-request-id" in streamed.headers

    accepted_stream = client.post(
        "/api/v1/owners",
        content=iter(
            (
                b'{"name":"Streamed Owner",',
                b'"contact_email":"streamed@example.com"}',
            )
        ),
        headers={"Content-Type": "application/json"},
    )
    assert accepted_stream.status_code == 201, accepted_stream.text


def test_openapi_documents_strict_count_date_and_amenity_shapes() -> None:
    schema = _client().get("/openapi.json").json()
    components = schema["components"]["schemas"]

    room_type = components["RoomTypeRequest"]["properties"]
    assert room_type["total_units"]["type"] == "integer"
    assert room_type["guests_per_unit"]["type"] == "integer"
    for model_name in ("RoomTypeRequest", "CreatePropertyRequest"):
        amenities = components[model_name]["properties"]["amenities"]
        assert amenities["maxItems"] == 100
        assert amenities["items"]["minLength"] == 1
        assert amenities["items"]["maxLength"] == 120

    booking = components["CreateBookingRequest"]["properties"]
    assert booking["guest_count"]["type"] == "integer"
    assert booking["check_in"] == {"type": "string", "format": "date", "title": "Check In"}
    assert booking["check_out"] == {
        "type": "string",
        "format": "date",
        "title": "Check Out",
    }

    search_parameters = schema["paths"]["/api/v1/properties/search"]["get"]["parameters"]
    amenity_parameter = next(item for item in search_parameters if item["name"] == "amenities")
    amenity_schema = next(
        branch for branch in amenity_parameter["schema"]["anyOf"] if branch.get("type") == "array"
    )
    assert amenity_schema["maxItems"] == 100
    assert amenity_schema["items"]["minLength"] == 1
    assert amenity_schema["items"]["maxLength"] == 120
