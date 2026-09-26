"""Swagger-facing manifest for the preloaded demonstration catalogue."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlencode
from uuid import UUID

from pydantic import BaseModel

from hotieler.api.schemas import (
    CreateBookingRequest,
    OwnerResponse,
    ProcessPaymentRequest,
    PropertyResponse,
    owner_response,
    property_response,
)
from hotieler.demo_data import DemoDataSnapshot, DemoSearch
from hotieler.domain.enums import MockPaymentOutcome, PaymentMethod


class DemoSearchResponse(BaseModel):
    label: str
    request_path: str
    city: str
    locality: str | None
    check_in: date
    check_out: date
    guest_count: int
    amenities: list[str]
    min_star_rating: str | None
    suggested_property_id: UUID
    suggested_room_type_id: UUID


class DemoPaymentExampleResponse(BaseModel):
    label: str
    idempotency_key_template: str
    request: ProcessPaymentRequest


class DemoDataResponse(BaseModel):
    enabled: bool
    notice: str
    generated_for_date: date | None
    owner: OwnerResponse | None
    properties: list[PropertyResponse]
    sample_searches: list[DemoSearchResponse]
    sample_booking: CreateBookingRequest | None
    payment_examples: list[DemoPaymentExampleResponse]
    workflow_steps: list[str]
    reset_command: str


def _search_response(search: DemoSearch) -> DemoSearchResponse:
    query = search.query
    query_items: list[tuple[str, str]] = [
        ("city", query.city),
        ("check_in", query.stay.check_in.isoformat()),
        ("check_out", query.stay.check_out.isoformat()),
        ("guest_count", str(query.guest_count)),
    ]
    if query.locality is not None:
        query_items.append(("locality", query.locality))
    query_items.extend(("amenities", amenity) for amenity in sorted(query.amenities))
    if query.min_star_rating is not None:
        query_items.append(("min_star_rating", format(query.min_star_rating, "f")))

    return DemoSearchResponse(
        label=search.label,
        request_path=f"/api/v1/properties/search?{urlencode(query_items)}",
        city=query.city,
        locality=query.locality,
        check_in=query.stay.check_in,
        check_out=query.stay.check_out,
        guest_count=query.guest_count,
        amenities=sorted(query.amenities),
        min_star_rating=(
            format(query.min_star_rating, "f") if query.min_star_rating is not None else None
        ),
        suggested_property_id=search.suggested_property_id,
        suggested_room_type_id=search.suggested_room_type_id,
    )


def demo_data_response(snapshot: DemoDataSnapshot) -> DemoDataResponse:
    searches = [_search_response(search) for search in snapshot.sample_searches]
    primary = searches[0]
    sample_booking = CreateBookingRequest(
        property_id=primary.suggested_property_id,
        room_type_id=primary.suggested_room_type_id,
        check_in=primary.check_in,
        check_out=primary.check_out,
        guest_count=primary.guest_count,
    )
    return DemoDataResponse(
        enabled=True,
        notice=(
            "This catalogue is stored only in this API process. Restarting the container "
            "clears bookings and recreates fresh demo IDs."
        ),
        generated_for_date=snapshot.generated_for_date,
        owner=owner_response(snapshot.owner),
        properties=[property_response(property_) for property_ in snapshot.properties],
        sample_searches=searches,
        sample_booking=sample_booking,
        payment_examples=[
            DemoPaymentExampleResponse(
                label="Approve a booking",
                idempotency_key_template="demo-approved-<booking-id>",
                request=ProcessPaymentRequest(
                    method=PaymentMethod.CARD,
                    mock_outcome=MockPaymentOutcome.APPROVED,
                ),
            ),
            DemoPaymentExampleResponse(
                label="Reject a booking and release its inventory",
                idempotency_key_template="demo-rejected-<booking-id>",
                request=ProcessPaymentRequest(
                    method=PaymentMethod.UPI,
                    mock_outcome=MockPaymentOutcome.REJECTED,
                ),
            ),
        ],
        workflow_steps=[
            "Run GET /api/v1/demo-data and copy one sample search.",
            "Run GET /api/v1/properties/search with those query values.",
            "Run POST /api/v1/bookings with sample_booking.",
            "Run POST /api/v1/bookings/{booking_id}/payments with an Idempotency-Key.",
            "Replay the same payment key, or cancel a confirmed booking.",
            "Use the restart command to clear all bookings and reseed the catalogue.",
        ],
        reset_command="./run.sh restart --no-open",
    )


def disabled_demo_data_response() -> DemoDataResponse:
    return DemoDataResponse(
        enabled=False,
        notice="Demo data is disabled for this application instance.",
        generated_for_date=None,
        owner=None,
        properties=[],
        sample_searches=[],
        sample_booking=None,
        payment_examples=[],
        workflow_steps=[],
        reset_command="./run.sh restart --no-open",
    )
