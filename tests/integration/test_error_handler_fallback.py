"""Integration coverage for the safe fallback of the public error hierarchy."""

from fastapi.testclient import TestClient

from hotieler.domain.errors import HotielerError
from hotieler.main import create_app


def test_unclassified_hotieler_error_uses_the_safe_internal_status_fallback() -> None:
    app = create_app()

    @app.get("/test-only-unclassified-hotieler-error")
    def fail_with_public_base_error() -> None:
        raise HotielerError()

    response = TestClient(app, raise_server_exceptions=False).get(
        "/test-only-unclassified-hotieler-error"
    )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "HOTIELER_ERROR",
            "message": "The request could not be completed.",
            "details": {},
        }
    }
    assert "x-request-id" in response.headers
