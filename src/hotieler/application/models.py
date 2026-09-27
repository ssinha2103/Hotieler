"""Framework-neutral commands and results for application use cases."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, DecimalException
from uuid import UUID

from hotieler.domain.entities import Booking, PaymentRecord, Property, RoomType
from hotieler.domain.enums import MockPaymentOutcome, PaymentMethod, PaymentStatus
from hotieler.domain.errors import DomainValidationError
from hotieler.domain.value_objects import Money, StayPeriod


@dataclass(frozen=True, slots=True)
class CreateOwnerCommand:
    name: str
    contact_email: str


@dataclass(frozen=True, slots=True)
class RoomTypeInput:
    name: str
    total_units: int
    guests_per_unit: int
    nightly_rate: Money
    amenities: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class CreatePropertyCommand:
    owner_id: UUID
    name: str
    city: str
    locality: str
    address: str
    star_rating: Decimal
    amenities: frozenset[str]
    room_types: tuple[RoomTypeInput, ...]


@dataclass(frozen=True, slots=True)
class SearchQuery:
    city: str
    stay: StayPeriod
    guest_count: int
    locality: str | None = None
    min_price: Money | None = None
    max_price: Money | None = None
    amenities: frozenset[str] = field(default_factory=frozenset)
    min_star_rating: Decimal | None = None

    def __post_init__(self) -> None:
        if self.min_star_rating is None:
            return
        try:
            rating = (
                self.min_star_rating
                if isinstance(self.min_star_rating, Decimal)
                else Decimal(str(self.min_star_rating))
            )
        except (DecimalException, ValueError) as exc:
            raise DomainValidationError("Minimum star rating must be numeric.") from exc
        if not rating.is_finite():
            raise DomainValidationError("Minimum star rating must be finite.")
        object.__setattr__(self, "min_star_rating", rating)


@dataclass(frozen=True, slots=True)
class AvailabilityQuote:
    property: Property
    room_type: RoomType
    required_units: int
    available_units: int
    total_price: Money


@dataclass(frozen=True, slots=True)
class CreateBookingCommand:
    property_id: UUID
    room_type_id: UUID
    stay: StayPeriod
    guest_count: int


@dataclass(frozen=True, slots=True)
class ProcessPaymentCommand:
    booking_id: UUID
    method: PaymentMethod
    mock_outcome: MockPaymentOutcome
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class PaymentProcessorResult:
    status: PaymentStatus
    provider_reference: str
    processed_at: datetime


@dataclass(frozen=True, slots=True)
class PaymentCommandResult:
    booking: Booking
    payment: PaymentRecord
    replayed: bool
