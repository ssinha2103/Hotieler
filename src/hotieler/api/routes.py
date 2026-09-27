"""Thin FastAPI routes translating HTTP data into application commands."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Body, Header, Path, Query, Request, status

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
from hotieler.domain.value_objects import Money, StayPeriod

router = APIRouter(prefix="/api/v1")

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {
        "model": ErrorResponse,
        "description": "The requested resource does not exist.",
        "content": {
            "application/json": {
                "examples": {
                    "not_found": {
                        "value": {
                            "error": {
                                "code": "RESOURCE_NOT_FOUND",
                                "message": "Booking was not found.",
                                "details": {},
                            }
                        }
                    }
                }
            }
        },
    },
    422: {
        "model": ErrorResponse,
        "description": "The request or domain input is invalid.",
        "content": {
            "application/json": {
                "examples": {
                    "validation_error": {
                        "value": {
                            "error": {
                                "code": "REQUEST_VALIDATION_ERROR",
                                "message": "The request could not be validated.",
                                "details": {"errors": []},
                            }
                        }
                    }
                }
            }
        },
    },
    500: {
        "model": ErrorResponse,
        "description": "An unexpected error was hidden behind the stable public envelope.",
        "content": {
            "application/json": {
                "examples": {
                    "internal_error": {
                        "value": {
                            "error": {
                                "code": "INTERNAL_SERVER_ERROR",
                                "message": "An unexpected internal error occurred.",
                                "details": {},
                            }
                        }
                    }
                }
            }
        },
    },
}

BOOKING_CONFLICT_RESPONSE: dict[str, Any] = {
    "model": ErrorResponse,
    "description": "The requested property/room pair or inventory state prevents booking.",
    "content": {
        "application/json": {
            "examples": {
                "inventory_unavailable": {
                    "value": {
                        "error": {
                            "code": "ROOM_INVENTORY_UNAVAILABLE",
                            "message": "The requested room inventory is no longer available.",
                            "details": {},
                        }
                    }
                },
                "property_room_mismatch": {
                    "value": {
                        "error": {
                            "code": "PROPERTY_ROOM_MISMATCH",
                            "message": "The room type does not belong to the requested property.",
                            "details": {},
                        }
                    }
                },
            }
        }
    },
}

PAYMENT_CONFLICT_RESPONSE: dict[str, Any] = {
    "model": ErrorResponse,
    "description": "The idempotency key or booking lifecycle state prevents payment.",
    "content": {
        "application/json": {
            "examples": {
                "idempotency_conflict": {
                    "value": {
                        "error": {
                            "code": "IDEMPOTENCY_KEY_CONFLICT",
                            "message": (
                                "The idempotency key was already used for a different request."
                            ),
                            "details": {},
                        }
                    }
                },
                "invalid_transition": {
                    "value": {
                        "error": {
                            "code": "INVALID_BOOKING_TRANSITION",
                            "message": "The booking cannot transition from its current state.",
                            "details": {},
                        }
                    }
                },
            }
        }
    },
}

CANCELLATION_CONFLICT_RESPONSE: dict[str, Any] = {
    "model": ErrorResponse,
    "description": "The booking lifecycle or stay dates prevent cancellation.",
    "content": {
        "application/json": {
            "examples": {
                "cancellation_not_allowed": {
                    "value": {
                        "error": {
                            "code": "CANCELLATION_NOT_ALLOWED",
                            "message": "The booking can no longer be cancelled.",
                            "details": {},
                        }
                    }
                },
                "invalid_transition": {
                    "value": {
                        "error": {
                            "code": "INVALID_BOOKING_TRANSITION",
                            "message": "The booking cannot transition from its current state.",
                            "details": {},
                        }
                    }
                },
            }
        }
    },
}

_PAYMENT_RESPONSE_VALUE: dict[str, Any] = {
    "booking": {
        "id": "33333333-3333-4333-8333-333333333333",
        "property_id": "11111111-1111-4111-8111-111111111111",
        "room_type_id": "22222222-2222-4222-8222-222222222222",
        "stay": {"check_in": "2030-12-20", "check_out": "2030-12-22", "nights": 2},
        "guest_count": 2,
        "required_units": 1,
        "total_price": {"amount": "7000.00", "currency": "INR"},
        "status": "CONFIRMED",
        "payment_id": "44444444-4444-4444-8444-444444444444",
        "cancellation": None,
        "created_at": "2030-12-01T10:00:00Z",
        "updated_at": "2030-12-01T10:01:00Z",
    },
    "payment": {
        "id": "44444444-4444-4444-8444-444444444444",
        "booking_id": "33333333-3333-4333-8333-333333333333",
        "method": "CARD",
        "amount": {"amount": "7000.00", "currency": "INR"},
        "status": "APPROVED",
        "mock_outcome": "APPROVED",
        "provider_reference": "MOCK-CARD-EXAMPLE",
        "idempotency_key": "payment-attempt-001",
        "booking_status_after": "CONFIRMED",
        "created_at": "2030-12-01T10:01:00Z",
    },
    "replayed": False,
}

_REJECTED_PAYMENT_RESPONSE_VALUE: dict[str, Any] = {
    **_PAYMENT_RESPONSE_VALUE,
    "booking": {
        **_PAYMENT_RESPONSE_VALUE["booking"],
        "status": "PAYMENT_FAILED",
    },
    "payment": {
        **_PAYMENT_RESPONSE_VALUE["payment"],
        "method": "UPI",
        "status": "REJECTED",
        "mock_outcome": "REJECTED",
        "provider_reference": "MOCK-UPI-EXAMPLE",
        "booking_status_after": "PAYMENT_FAILED",
    },
}

PAYMENT_RESPONSE_EXAMPLES = {
    "processed": {
        "summary": "An approved payment was processed",
        "value": _PAYMENT_RESPONSE_VALUE,
    },
    "rejected": {
        "summary": "A rejected payment was processed and released inventory",
        "value": _REJECTED_PAYMENT_RESPONSE_VALUE,
    },
    "replayed": {
        "summary": "The same idempotent result was replayed",
        "value": {**_PAYMENT_RESPONSE_VALUE, "replayed": True},
    },
}


def _container(request: Request) -> AppContainer:
    return cast(AppContainer, request.app.state.container)


@router.post(
    "/owners",
    tags=["Owners"],
    response_model=OwnerResponse,
    status_code=status.HTTP_201_CREATED,
    responses={422: ERROR_RESPONSES[422], 500: ERROR_RESPONSES[500]},
    summary="Create an owner account",
)
def create_owner(payload: CreateOwnerRequest, request: Request) -> OwnerResponse:
    owner = _container(request).catalog_service.create_owner(
        CreateOwnerCommand(name=payload.name, contact_email=str(payload.contact_email))
    )
    return owner_response(owner)


@router.post(
    "/owners/{owner_id}/properties",
    tags=["Properties & Search"],
    response_model=PropertyResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: ERROR_RESPONSES[404],
        422: ERROR_RESPONSES[422],
        500: ERROR_RESPONSES[500],
    },
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
    responses={422: ERROR_RESPONSES[422], 500: ERROR_RESPONSES[500]},
    summary="Search available room types",
    description=(
        "Returns an advisory availability snapshot. Booking creation performs the authoritative "
        "inventory check again under the room-type lock."
    ),
)
def search_properties(
    request: Request,
    city: Annotated[str, Query(min_length=1, max_length=120)],
    check_in: Annotated[date, Query()],
    check_out: Annotated[date, Query()],
    guest_count: Annotated[int, Query(gt=0, le=10_000)],
    locality: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    min_price: Annotated[
        Decimal | None,
        Query(ge=0, max_digits=14, decimal_places=2),
    ] = None,
    max_price: Annotated[
        Decimal | None,
        Query(ge=0, max_digits=14, decimal_places=2),
    ] = None,
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
    responses={
        404: ERROR_RESPONSES[404],
        409: BOOKING_CONFLICT_RESPONSE,
        422: ERROR_RESPONSES[422],
        500: ERROR_RESPONSES[500],
    },
    summary="Create a pending booking and reserve inventory",
    description=(
        "Rechecks availability atomically, snapshots the server-side price, and creates a "
        "PENDING_PAYMENT inventory hold."
    ),
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
    responses={
        404: ERROR_RESPONSES[404],
        422: ERROR_RESPONSES[422],
        500: ERROR_RESPONSES[500],
    },
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
    responses={
        200: {
            "description": "Processed or safely replayed payment result.",
            "content": {"application/json": {"examples": PAYMENT_RESPONSE_EXAMPLES}},
        },
        404: ERROR_RESPONSES[404],
        409: PAYMENT_CONFLICT_RESPONSE,
        422: ERROR_RESPONSES[422],
        500: ERROR_RESPONSES[500],
    },
    summary="Execute a deterministic mock payment",
    description=(
        "Requires Idempotency-Key. APPROVED and REJECTED are both processed outcomes returned "
        "with HTTP 200. Reusing the same key and fingerprint replays the original result; a "
        "changed fingerprint returns HTTP 409."
    ),
)
def process_payment(
    payload: Annotated[
        ProcessPaymentRequest,
        Body(
            openapi_examples={
                "approved": {
                    "summary": "Simulate an approved card payment",
                    "value": {"method": "CARD", "mock_outcome": "APPROVED"},
                },
                "rejected": {
                    "summary": "Simulate a rejected UPI payment",
                    "value": {"method": "UPI", "mock_outcome": "REJECTED"},
                },
            }
        ),
    ],
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
    responses={
        404: ERROR_RESPONSES[404],
        409: CANCELLATION_CONFLICT_RESPONSE,
        422: ERROR_RESPONSES[422],
        500: ERROR_RESPONSES[500],
    },
    summary="Cancel a booking and record its refund calculation",
    description=(
        "Applies the injected cancellation policy, releases inventory, and records the refund "
        "calculation. Repeating an already completed cancellation returns the stored result."
    ),
)
def cancel_booking(
    request: Request,
    booking_id: Annotated[UUID, Path()],
) -> BookingResponse:
    return booking_response(_container(request).cancellation_service.cancel(booking_id))
