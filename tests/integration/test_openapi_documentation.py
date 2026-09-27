"""Contract tests for the interactive API documentation."""

from fastapi.testclient import TestClient

from hotieler.main import create_app


def test_swagger_ui_is_available_and_uses_the_public_openapi_schema() -> None:
    client = TestClient(create_app())

    response = client.get("/docs")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Swagger UI" in response.text
    assert "Hotel Booking API" in response.text
    assert "url: '/openapi.json'" in response.text


def test_openapi_schema_exposes_the_complete_public_http_contract() -> None:
    client = TestClient(create_app())

    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    schema = response.json()
    assert schema["info"] == {
        "title": "Hotel Booking API",
        "description": (
            "Backend-only hotel discovery, booking, deterministic mock payment, "
            "and cancellation service. All data is process-local and in memory."
        ),
        "version": "1.0.0",
    }

    expected_operations = {
        "/health": {"get"},
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

    payment_operation = schema["paths"]["/api/v1/bookings/{booking_id}/payments"]["post"]
    payment_request_examples = payment_operation["requestBody"]["content"]["application/json"][
        "examples"
    ]
    assert set(payment_request_examples) == {"approved", "rejected"}
    assert payment_request_examples["approved"]["value"]["mock_outcome"] == "APPROVED"
    assert payment_request_examples["rejected"]["value"]["mock_outcome"] == "REJECTED"

    payment_response_examples = payment_operation["responses"]["200"]["content"][
        "application/json"
    ]["examples"]
    assert set(payment_response_examples) == {"processed", "rejected", "replayed"}
    assert payment_response_examples["processed"]["value"]["replayed"] is False
    assert payment_response_examples["processed"]["value"]["payment"]["status"] == "APPROVED"
    assert payment_response_examples["processed"]["value"]["booking"]["status"] == "CONFIRMED"
    assert payment_response_examples["rejected"]["value"]["replayed"] is False
    assert payment_response_examples["rejected"]["value"]["payment"]["status"] == "REJECTED"
    assert payment_response_examples["rejected"]["value"]["booking"]["status"] == "PAYMENT_FAILED"
    assert payment_response_examples["replayed"]["value"]["replayed"] is True

    booking_operation = schema["paths"]["/api/v1/bookings"]["post"]
    cancellation_operation = schema["paths"]["/api/v1/bookings/{booking_id}/cancel"]["post"]

    def conflict_examples(operation: dict[str, object]) -> dict[str, object]:
        return operation["responses"]["409"]["content"]["application/json"][  # type: ignore[index]
            "examples"
        ]

    assert set(conflict_examples(booking_operation)) == {
        "inventory_unavailable",
        "property_room_mismatch",
    }
    assert set(conflict_examples(payment_operation)) == {
        "idempotency_conflict",
        "invalid_transition",
    }
    assert set(conflict_examples(cancellation_operation)) == {
        "cancellation_not_allowed",
        "invalid_transition",
    }

    operations_without_conflicts = (
        schema["paths"]["/api/v1/owners"]["post"],
        schema["paths"]["/api/v1/owners/{owner_id}/properties"]["post"],
        schema["paths"]["/api/v1/properties/search"]["get"],
        schema["paths"]["/api/v1/bookings/{booking_id}"]["get"],
    )
    assert all("409" not in operation["responses"] for operation in operations_without_conflicts)
    assert "same key and fingerprint" in payment_operation["description"]

    owner_schema = schema["components"]["schemas"]["CreateOwnerRequest"]
    assert owner_schema["properties"]["contact_email"]["format"] == "email"
    assert owner_schema["examples"]

    body_operations = (
        schema["paths"]["/api/v1/owners"]["post"],
        schema["paths"]["/api/v1/owners/{owner_id}/properties"]["post"],
        schema["paths"]["/api/v1/bookings"]["post"],
        payment_operation,
    )
    assert all("400" in operation["responses"] for operation in body_operations)
    assert all("413" in operation["responses"] for operation in body_operations)

    money_schema = schema["components"]["schemas"]["MoneyInput"]["properties"]
    numeric_amount = next(
        branch for branch in money_schema["amount"]["anyOf"] if branch.get("type") == "number"
    )
    assert numeric_amount["multipleOf"] == 0.01
    assert numeric_amount["maximum"] == 999_999_999_999.99
    assert money_schema["currency"]["enum"] == ["INR"]
    assert schema["components"]["schemas"]["MoneyResponse"]["properties"]["currency"] == {
        "type": "string",
        "const": "INR",
        "title": "Currency",
    }

    property_schema = schema["components"]["schemas"]["CreatePropertyRequest"]
    numeric_rating = next(
        branch
        for branch in property_schema["properties"]["star_rating"]["anyOf"]
        if branch.get("type") == "number"
    )
    assert numeric_rating["multipleOf"] == 0.1

    search_parameters = schema["paths"]["/api/v1/properties/search"]["get"]["parameters"]
    parameters_by_name = {parameter["name"]: parameter for parameter in search_parameters}
    for parameter_name in ("min_price", "max_price"):
        numeric_price = next(
            branch
            for branch in parameters_by_name[parameter_name]["schema"]["anyOf"]
            if branch.get("type") == "number"
        )
        assert numeric_price["multipleOf"] == 0.01
        assert numeric_price["maximum"] == 999_999_999_999.99
    numeric_minimum_rating = next(
        branch
        for branch in parameters_by_name["min_star_rating"]["schema"]["anyOf"]
        if branch.get("type") == "number"
    )
    assert numeric_minimum_rating["multipleOf"] == 0.1

    booking_response_schema = schema["components"]["schemas"]["BookingResponse"]
    assert "cancellation" not in booking_response_schema["required"]
    cancellation_schema = booking_response_schema["properties"]["cancellation"]
    assert {"type": "null"} in cancellation_schema["anyOf"]


def test_redoc_is_available_as_a_secondary_contract_view() -> None:
    client = TestClient(create_app())

    response = client.get("/redoc")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "ReDoc" in response.text
    assert "Hotel Booking API" in response.text
    assert 'spec-url="/openapi.json"' in response.text
