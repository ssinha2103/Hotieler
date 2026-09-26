"""End-to-end coverage for the Swagger-visible demo catalogue."""

from datetime import UTC, date, datetime
from uuid import NAMESPACE_URL

from fastapi.testclient import TestClient

from hotieler.container import AppContainer, build_container
from hotieler.infrastructure.clock import DeterministicIdGenerator, FixedClock
from hotieler.main import create_app

AS_OF = date(2030, 1, 1)


def _demo_client(*, enabled: bool = True) -> tuple[TestClient, AppContainer]:
    container = build_container(
        clock=FixedClock(datetime(2030, 1, 1, 10, tzinfo=UTC)),
        ids=DeterministicIdGenerator(NAMESPACE_URL, prefix="demo-api"),
    )
    app = create_app(container, seed_demo=enabled, demo_as_of=AS_OF)
    return TestClient(app), container


def test_demo_manifest_exposes_seeded_accounts_properties_and_ready_requests() -> None:
    client, container = _demo_client()

    response = client.get("/api/v1/demo-data")

    assert response.status_code == 200
    manifest = response.json()
    assert manifest["enabled"] is True
    assert manifest["generated_for_date"] == "2030-01-01"
    assert manifest["owner"]["name"] == "Hotieler Demo Hospitality"
    assert len(manifest["properties"]) == 2
    assert sum(len(property_["room_types"]) for property_ in manifest["properties"]) == 4
    assert {property_["city"] for property_ in manifest["properties"]} == {
        "Bengaluru",
        "Goa",
    }
    assert any(
        room["total_units"] == 1
        for property_ in manifest["properties"]
        for room in property_["room_types"]
    )
    assert len(manifest["sample_searches"]) == 2
    assert (
        manifest["sample_booking"]["property_id"]
        == manifest["sample_searches"][0]["suggested_property_id"]
    )
    assert [example["request"]["mock_outcome"] for example in manifest["payment_examples"]] == [
        "APPROVED",
        "REJECTED",
    ]
    assert manifest["reset_command"] == "./run.sh restart --no-open"
    assert container.booking_repository.list() == []


def test_seeded_one_room_flow_supports_rejection_replay_approval_and_cancellation() -> None:
    client, _container = _demo_client()
    manifest = client.get("/api/v1/demo-data").json()
    goa_search = manifest["sample_searches"][1]
    search_path = goa_search["request_path"]

    initial_quotes = client.get(search_path)
    assert initial_quotes.status_code == 200
    assert [quote["room_type"]["id"] for quote in initial_quotes.json()] == [
        goa_search["suggested_room_type_id"]
    ]

    booking_request = {
        "property_id": goa_search["suggested_property_id"],
        "room_type_id": goa_search["suggested_room_type_id"],
        "check_in": goa_search["check_in"],
        "check_out": goa_search["check_out"],
        "guest_count": goa_search["guest_count"],
    }
    rejected_booking = client.post("/api/v1/bookings", json=booking_request)
    assert rejected_booking.status_code == 201
    assert client.get(search_path).json() == []

    rejected_example = manifest["payment_examples"][1]
    rejected = client.post(
        f"/api/v1/bookings/{rejected_booking.json()['id']}/payments",
        headers={"Idempotency-Key": "demo-rejected-flow"},
        json=rejected_example["request"],
    )
    assert rejected.status_code == 200
    assert rejected.json()["booking"]["status"] == "PAYMENT_FAILED"
    assert len(client.get(search_path).json()) == 1

    approved_booking = client.post("/api/v1/bookings", json=booking_request)
    assert approved_booking.status_code == 201
    payment_path = f"/api/v1/bookings/{approved_booking.json()['id']}/payments"
    approved_example = manifest["payment_examples"][0]
    approved = client.post(
        payment_path,
        headers={"Idempotency-Key": "demo-approved-flow"},
        json=approved_example["request"],
    )
    replayed = client.post(
        payment_path,
        headers={"Idempotency-Key": "demo-approved-flow"},
        json=approved_example["request"],
    )
    assert approved.status_code == 200
    assert approved.json()["booking"]["status"] == "CONFIRMED"
    assert replayed.status_code == 200
    assert replayed.json()["replayed"] is True
    assert replayed.json()["payment"]["id"] == approved.json()["payment"]["id"]

    cancelled = client.post(f"/api/v1/bookings/{approved_booking.json()['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["cancellation"]["refund_percentage"] == "100"
    assert len(client.get(search_path).json()) == 1


def test_demo_manifest_reports_when_seeding_is_disabled() -> None:
    client, container = _demo_client(enabled=False)

    response = client.get("/api/v1/demo-data")

    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "notice": "Demo data is disabled for this application instance.",
        "generated_for_date": None,
        "owner": None,
        "properties": [],
        "sample_searches": [],
        "sample_booking": None,
        "payment_examples": [],
        "workflow_steps": [],
        "reset_command": "./run.sh restart --no-open",
    }
    assert container.property_repository.list_properties() == []


def test_environment_flag_seeds_the_default_runtime_app(monkeypatch) -> None:
    monkeypatch.setenv("HOTIELER_SEED_DEMO_DATA", "true")

    client = TestClient(create_app(demo_as_of=AS_OF))

    manifest = client.get("/api/v1/demo-data").json()
    assert manifest["enabled"] is True
    assert manifest["generated_for_date"] == "2030-01-01"
