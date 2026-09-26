"""Thin FastAPI routes translating HTTP data into application commands."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Request, status

from hotieler.api.demo_schemas import (
    DemoDataResponse,
    demo_data_response,
    disabled_demo_data_response,
)
from hotieler.api.schemas import (
    AvailabilityResponse,
    BookingResponse,
    CreateBookingRequest,
    CreateOwnerRequest,
    CreatePropertyRequest,
    ErrorResponse,
    OwnerResponse,
    PaymentResultResponse,
    ProcessPaymentRequest,
    PropertyResponse,
    availability_response,
    booking_response,
    owner_response,
    payment_result_response,
    property_response,
)
from hotieler.application.models import (
    CreateBookingCommand,
    CreateOwnerCommand,
    CreatePropertyCommand,
    ProcessPaymentCommand,
    RoomTypeInput,
    SearchQuery,
)
from hotieler.container import AppContainer
from hotieler.demo_data import DemoDataSnapshot
from hotieler.domain.value_objects import Money, StayPeriod

router = APIRouter(prefix="/api/v1")

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "The requested resource does not exist."},
    409: {"model": ErrorResponse, "description": "The command conflicts with current state."},
    422: {"model": ErrorResponse, "description": "The request or domain input is invalid."},
}


def _container(request: Request) -> AppContainer:
    return cast(AppContainer, request.app.state.container)


@router.get(
    "/demo-data",
    tags=["Demo"],
    response_model=DemoDataResponse,
    summary="Get preloaded demo IDs and guided test requests",
    description=(
        "Returns the Docker demo catalogue, ready-to-copy availability searches, a booking "
        "request, payment examples, and reset guidance. No bookings are pre-created."
    ),
)
def get_demo_data(request: Request) -> DemoDataResponse:
    snapshot = cast(DemoDataSnapshot | None, request.app.state.demo_data)
    if snapshot is None:
        return disabled_demo_data_response()
    return demo_data_response(snapshot)


@router.post(
    "/owners",
    tags=["Owners"],
    response_model=OwnerResponse,
    status_code=status.HTTP_201_CREATED,
    responses={422: ERROR_RESPONSES[422]},
    summary="Create an owner account",
)
def create_owner(payload: CreateOwnerRequest, request: Request) -> OwnerResponse:
    owner = _container(request).catalog_service.create_owner(
        CreateOwnerCommand(name=payload.name, contact_email=payload.contact_email)
    )
    return owner_response(owner)


@router.post(
    "/owners/{owner_id}/properties",
    tags=["Properties & Search"],
    response_model=PropertyResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    summary="Add a property and its room types",
)
def create_property(
    payload: CreatePropertyRequest,
    request: Request,
    owner_id: Annotated[UUID, Path()],
) -> PropertyResponse:
    command = CreatePropertyCommand(
        owner_id=owner_id,
        name=payload.name,
        city=payload.city,
        locality=payload.locality,
        address=payload.address,
        star_rating=payload.star_rating,
        amenities=frozenset(payload.amenities),
        room_types=tuple(
            RoomTypeInput(
                name=room.name,
                total_units=room.total_units,
                guests_per_unit=room.guests_per_unit,
                nightly_rate=Money(room.nightly_rate.amount, room.nightly_rate.currency),
                amenities=frozenset(room.amenities),
            )
            for room in payload.room_types
        ),
    )
    property_ = _container(request).catalog_service.create_property(command)
    return property_response(property_)


@router.get(
    "/properties/search",
    tags=["Properties & Search"],
    response_model=list[AvailabilityResponse],
    responses={422: ERROR_RESPONSES[422]},
    summary="Search available room types",
)
def search_properties(
    request: Request,
    city: Annotated[str, Query(min_length=1, max_length=120)],
    check_in: Annotated[date, Query()],
    check_out: Annotated[date, Query()],
    guest_count: Annotated[int, Query(gt=0, le=10_000)],
    locality: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    min_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    amenities: Annotated[list[str] | None, Query()] = None,
    min_star_rating: Annotated[Decimal | None, Query(ge=1, le=5)] = None,
) -> list[AvailabilityResponse]:
    query = SearchQuery(
        city=city,
        stay=StayPeriod(check_in, check_out),
        guest_count=guest_count,
        locality=locality,
        min_price=Money(min_price, "INR") if min_price is not None else None,
        max_price=Money(max_price, "INR") if max_price is not None else None,
        amenities=frozenset(amenities or ()),
        min_star_rating=min_star_rating,
    )
    quotes = _container(request).availability_service.search(query)
    return [availability_response(quote) for quote in quotes]


@router.post(
    "/bookings",
    tags=["Bookings"],
    response_model=BookingResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
    summary="Create a pending booking and reserve inventory",
)
def create_booking(payload: CreateBookingRequest, request: Request) -> BookingResponse:
    booking = _container(request).booking_service.create(
        CreateBookingCommand(
            property_id=payload.property_id,
            room_type_id=payload.room_type_id,
            stay=StayPeriod(payload.check_in, payload.check_out),
            guest_count=payload.guest_count,
        )
    )
    return booking_response(booking)


@router.get(
    "/bookings/{booking_id}",
    tags=["Bookings"],
    response_model=BookingResponse,
    responses={404: ERROR_RESPONSES[404], 422: ERROR_RESPONSES[422]},
    summary="Fetch current booking state",
)
def get_booking(
    request: Request,
    booking_id: Annotated[UUID, Path()],
) -> BookingResponse:
    return booking_response(_container(request).booking_service.get(booking_id))


@router.post(
    "/bookings/{booking_id}/payments",
    tags=["Payments"],
    response_model=PaymentResultResponse,
    responses=ERROR_RESPONSES,
    summary="Execute a deterministic mock payment",
)
def process_payment(
    payload: ProcessPaymentRequest,
    request: Request,
    booking_id: Annotated[UUID, Path()],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ],
) -> PaymentResultResponse:
    result = _container(request).payment_service.process(
        ProcessPaymentCommand(
            booking_id=booking_id,
            method=payload.method,
            mock_outcome=payload.mock_outcome,
            idempotency_key=idempotency_key.strip(),
        )
    )
    return payment_result_response(result)


@router.post(
    "/bookings/{booking_id}/cancel",
    tags=["Bookings"],
    response_model=BookingResponse,
    responses=ERROR_RESPONSES,
    summary="Cancel a booking and calculate its refund",
)
def cancel_booking(
    request: Request,
    booking_id: Annotated[UUID, Path()],
) -> BookingResponse:
    return booking_response(_container(request).cancellation_service.cancel(booking_id))
