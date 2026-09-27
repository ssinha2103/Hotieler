"""Pydantic request/response schemas and domain projection helpers."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Final
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    StrictInt,
    StringConstraints,
    field_validator,
)

from hotieler.application.models import AvailabilityQuote, PaymentCommandResult
from hotieler.domain.entities import (
    Booking,
    CancellationRecord,
    OwnerAccount,
    PaymentRecord,
    Property,
    RoomType,
)
from hotieler.domain.enums import (
    BookingStatus,
    MockPaymentOutcome,
    PaymentMethod,
    PaymentStatus,
    RefundStatus,
)
from hotieler.domain.value_objects import Money

AmenityLabel = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]

_ISO_DATE_PATTERN: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _require_iso_date_string(value: Any) -> str:
    if not isinstance(value, str) or _ISO_DATE_PATTERN.fullmatch(value) is None:
        raise ValueError("Date must be an ISO string in YYYY-MM-DD format.")
    return value


IsoDate = Annotated[date, BeforeValidator(_require_iso_date_string)]


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MoneyInput(_RequestModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"amount": "3500.00", "currency": "INR"}]}
    )

    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: str = Field(default="INR", min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.upper()
        if normalized != "INR":
            raise ValueError("Only INR is supported.")
        return normalized


class MoneyResponse(BaseModel):
    amount: str
    currency: str


class CreateOwnerRequest(_RequestModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "Northstar Hospitality",
                    "contact_email": "owner@northstar.example",
                }
            ]
        }
    )

    name: str = Field(min_length=1, max_length=120)
    contact_email: EmailStr = Field(max_length=254)


class OwnerResponse(BaseModel):
    id: UUID
    name: str
    contact_email: str
    created_at: datetime


class RoomTypeRequest(_RequestModel):
    name: str = Field(min_length=1, max_length=120)
    total_units: StrictInt = Field(gt=0, le=10_000)
    guests_per_unit: StrictInt = Field(gt=0, le=100)
    nightly_rate: MoneyInput
    amenities: list[AmenityLabel] = Field(default_factory=list, max_length=100)


class CreatePropertyRequest(_RequestModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "Northstar Bengaluru",
                    "city": "Bengaluru",
                    "locality": "Indiranagar",
                    "address": "100 Main Road",
                    "star_rating": "4.5",
                    "amenities": ["wifi", "pool"],
                    "room_types": [
                        {
                            "name": "Deluxe",
                            "total_units": 2,
                            "guests_per_unit": 2,
                            "nightly_rate": {"amount": "3500.00", "currency": "INR"},
                            "amenities": ["air conditioning"],
                        }
                    ],
                }
            ]
        }
    )

    name: str = Field(min_length=1, max_length=160)
    city: str = Field(min_length=1, max_length=120)
    locality: str = Field(min_length=1, max_length=120)
    address: str = Field(min_length=1, max_length=500)
    star_rating: Decimal = Field(ge=1, le=5, decimal_places=1)
    amenities: list[AmenityLabel] = Field(default_factory=list, max_length=100)
    room_types: list[RoomTypeRequest] = Field(min_length=1, max_length=100)


class RoomTypeResponse(BaseModel):
    id: UUID
    property_id: UUID
    name: str
    total_units: int
    guests_per_unit: int
    nightly_rate: MoneyResponse
    amenities: list[str]


class PropertyResponse(BaseModel):
    id: UUID
    owner_id: UUID
    name: str
    city: str
    locality: str
    address: str
    star_rating: str
    amenities: list[str]
    room_types: list[RoomTypeResponse]
    created_at: datetime


class AvailabilityResponse(BaseModel):
    property_id: UUID
    property_name: str
    city: str
    locality: str
    star_rating: str
    property_amenities: list[str]
    room_type: RoomTypeResponse
    required_units: int
    available_units: int
    total_price: MoneyResponse


class CreateBookingRequest(_RequestModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "property_id": "11111111-1111-4111-8111-111111111111",
                    "room_type_id": "22222222-2222-4222-8222-222222222222",
                    "check_in": "2030-12-20",
                    "check_out": "2030-12-22",
                    "guest_count": 2,
                }
            ]
        }
    )

    property_id: UUID
    room_type_id: UUID
    check_in: IsoDate
    check_out: IsoDate
    guest_count: StrictInt = Field(gt=0, le=10_000)


class StayResponse(BaseModel):
    check_in: date
    check_out: date
    nights: int


class CancellationResponse(BaseModel):
    cancelled_at: datetime
    refund_amount: MoneyResponse
    refund_percentage: str
    refund_status: RefundStatus


class BookingResponse(BaseModel):
    id: UUID
    property_id: UUID
    room_type_id: UUID
    stay: StayResponse
    guest_count: int
    required_units: int
    total_price: MoneyResponse
    status: BookingStatus
    payment_id: UUID | None
    cancellation: CancellationResponse | None = None
    created_at: datetime
    updated_at: datetime


class ProcessPaymentRequest(_RequestModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"method": "CARD", "mock_outcome": "APPROVED"},
                {"method": "UPI", "mock_outcome": "REJECTED"},
            ]
        }
    )

    method: PaymentMethod
    mock_outcome: MockPaymentOutcome


class PaymentResponse(BaseModel):
    id: UUID
    booking_id: UUID
    method: PaymentMethod
    amount: MoneyResponse
    status: PaymentStatus
    mock_outcome: MockPaymentOutcome
    provider_reference: str
    idempotency_key: str
    booking_status_after: BookingStatus
    created_at: datetime


class PaymentResultResponse(BaseModel):
    booking: BookingResponse
    payment: PaymentResponse
    replayed: bool


class HealthResponse(BaseModel):
    status: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any]


class ErrorResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "error": {
                        "code": "ROOM_INVENTORY_UNAVAILABLE",
                        "message": "The requested room inventory is no longer available.",
                        "details": {},
                    }
                }
            ]
        }
    )

    error: ErrorBody


def money_response(money: Money) -> MoneyResponse:
    return MoneyResponse(amount=format(money.amount, "f"), currency=money.currency)


def owner_response(owner: OwnerAccount) -> OwnerResponse:
    return OwnerResponse(
        id=owner.id,
        name=owner.name,
        contact_email=owner.contact_email,
        created_at=owner.created_at,
    )


def room_type_response(room_type: RoomType) -> RoomTypeResponse:
    return RoomTypeResponse(
        id=room_type.id,
        property_id=room_type.property_id,
        name=room_type.name,
        total_units=room_type.total_units,
        guests_per_unit=room_type.guests_per_unit,
        nightly_rate=money_response(room_type.nightly_rate),
        amenities=sorted(room_type.amenities),
    )


def property_response(property_: Property) -> PropertyResponse:
    return PropertyResponse(
        id=property_.id,
        owner_id=property_.owner_id,
        name=property_.name,
        city=property_.city,
        locality=property_.locality,
        address=property_.address,
        star_rating=format(property_.star_rating, "f"),
        amenities=sorted(property_.amenities),
        room_types=[room_type_response(room) for room in property_.room_types],
        created_at=property_.created_at,
    )


def availability_response(quote: AvailabilityQuote) -> AvailabilityResponse:
    return AvailabilityResponse(
        property_id=quote.property.id,
        property_name=quote.property.name,
        city=quote.property.city,
        locality=quote.property.locality,
        star_rating=format(quote.property.star_rating, "f"),
        property_amenities=sorted(quote.property.amenities),
        room_type=room_type_response(quote.room_type),
        required_units=quote.required_units,
        available_units=quote.available_units,
        total_price=money_response(quote.total_price),
    )


def cancellation_response(cancellation: CancellationRecord) -> CancellationResponse:
    return CancellationResponse(
        cancelled_at=cancellation.cancelled_at,
        refund_amount=money_response(cancellation.refund_amount),
        refund_percentage=format(cancellation.refund_percentage, "f"),
        refund_status=cancellation.refund_status,
    )


def booking_response(booking: Booking) -> BookingResponse:
    cancellation = (
        cancellation_response(booking.cancellation) if booking.cancellation is not None else None
    )
    return BookingResponse(
        id=booking.id,
        property_id=booking.property_id,
        room_type_id=booking.room_type_id,
        stay=StayResponse(
            check_in=booking.stay.check_in,
            check_out=booking.stay.check_out,
            nights=booking.stay.nights,
        ),
        guest_count=booking.guest_count,
        required_units=booking.required_units,
        total_price=money_response(booking.total_price),
        status=booking.status,
        payment_id=booking.payment_id,
        cancellation=cancellation,
        created_at=booking.created_at,
        updated_at=booking.updated_at,
    )


def payment_response(payment: PaymentRecord) -> PaymentResponse:
    return PaymentResponse(
        id=payment.id,
        booking_id=payment.booking_id,
        method=payment.method,
        amount=money_response(payment.amount),
        status=payment.status,
        mock_outcome=payment.mock_outcome,
        provider_reference=payment.provider_reference,
        idempotency_key=payment.idempotency_key,
        booking_status_after=payment.booking_status_after,
        created_at=payment.created_at,
    )


def payment_result_response(result: PaymentCommandResult) -> PaymentResultResponse:
    return PaymentResultResponse(
        booking=booking_response(result.booking),
        payment=payment_response(result.payment),
        replayed=result.replayed,
    )
