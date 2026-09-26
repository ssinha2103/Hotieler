"""Swagger grouping tests for the public HTTP resources."""

from fastapi.testclient import TestClient

from hotieler.main import create_app


def test_openapi_groups_operations_by_business_capability() -> None:
    schema = TestClient(create_app()).get("/openapi.json").json()

    assert [tag["name"] for tag in schema["tags"]] == [
        "Owners",
        "Properties & Search",
        "Bookings",
        "Payments",
        "Runtime",
    ]

    expected_tags = {
        ("/health", "get"): ["Runtime"],
        ("/api/v1/owners", "post"): ["Owners"],
        ("/api/v1/owners/{owner_id}/properties", "post"): ["Properties & Search"],
        ("/api/v1/properties/search", "get"): ["Properties & Search"],
        ("/api/v1/bookings", "post"): ["Bookings"],
        ("/api/v1/bookings/{booking_id}", "get"): ["Bookings"],
        ("/api/v1/bookings/{booking_id}/payments", "post"): ["Payments"],
        ("/api/v1/bookings/{booking_id}/cancel", "post"): ["Bookings"],
    }
    for (path, method), tags in expected_tags.items():
        assert schema["paths"][path][method]["tags"] == tags
