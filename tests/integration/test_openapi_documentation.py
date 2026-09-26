"""Contract tests for the interactive API documentation."""

from fastapi.testclient import TestClient

from hotieler.main import create_app


def test_swagger_ui_is_available_and_uses_the_public_openapi_schema() -> None:
    client = TestClient(create_app())

    response = client.get("/docs")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Swagger UI" in response.text
    assert "Hotieler Hotel Booking API" in response.text
    assert "url: '/openapi.json'" in response.text


def test_openapi_schema_exposes_the_complete_public_http_contract() -> None:
    client = TestClient(create_app())

    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    schema = response.json()
    assert schema["info"] == {
        "title": "Hotieler Hotel Booking API",
        "description": (
            "Backend-only hotel discovery, booking, deterministic mock payment, "
            "and cancellation service. All data is process-local and in memory."
        ),
        "version": "1.0.0",
    }

    expected_operations = {
        "/health": {"get"},
        "/api/v1/demo-data": {"get"},
        "/api/v1/owners": {"post"},
        "/api/v1/owners/{owner_id}/properties": {"post"},
        "/api/v1/properties/search": {"get"},
        "/api/v1/bookings": {"post"},
        "/api/v1/bookings/{booking_id}": {"get"},
        "/api/v1/bookings/{booking_id}/payments": {"post"},
        "/api/v1/bookings/{booking_id}/cancel": {"post"},
    }
    assert set(schema["paths"]) == set(expected_operations)
    for path, methods in expected_operations.items():
        assert set(schema["paths"][path]) == methods

    payment_parameters = schema["paths"]["/api/v1/bookings/{booking_id}/payments"]["post"][
        "parameters"
    ]
    idempotency_header = next(
        parameter
        for parameter in payment_parameters
        if parameter["in"] == "header" and parameter["name"] == "Idempotency-Key"
    )
    assert idempotency_header["required"] is True
    assert idempotency_header["schema"]["type"] == "string"


def test_redoc_is_available_as_a_secondary_contract_view() -> None:
    client = TestClient(create_app())

    response = client.get("/redoc")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "ReDoc" in response.text
    assert "Hotieler Hotel Booking API" in response.text
    assert 'spec-url="/openapi.json"' in response.text
