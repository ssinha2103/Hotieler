"""Entities that own identity and booking lifecycle behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from hotieler.domain.enums import (
    BookingStatus,
    MockPaymentOutcome,
    PaymentMethod,
    PaymentStatus,
    RefundStatus,
)
from hotieler.domain.errors import DomainValidationError, InvalidBookingTransitionError
from hotieler.domain.value_objects import Money, StayPeriod


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise DomainValidationError(f"{field_name} cannot be blank.")
    return normalized


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)


def normalize_amenities(values: frozenset[str] | set[str] | tuple[str, ...]) -> frozenset[str]:
    """Normalize amenity labels for deterministic matching and serialization."""

    return frozenset(value.strip().casefold() for value in values if value.strip())


@dataclass(frozen=True, slots=True)
class OwnerAccount:
    id: UUID
    name: str
    contact_email: str
    created_at: datetime

    def __post_init__(self) -> None:
        name = _required_text(self.name, "Owner name")
        email = _required_text(self.contact_email, "Contact email").casefold()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise DomainValidationError("Contact email must be valid.")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "contact_email", email)
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))


@dataclass(frozen=True, slots=True)
class RoomType:
    id: UUID
    property_id: UUID
    name: str
    total_units: int
    guests_per_unit: int
    nightly_rate: Money
    amenities: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.total_units <= 0:
            raise DomainValidationError("A room type must have at least one unit.")
        if self.guests_per_unit <= 0:
            raise DomainValidationError("Room capacity must be greater than zero.")
        object.__setattr__(self, "name", _required_text(self.name, "Room type name"))
        object.__setattr__(self, "amenities", normalize_amenities(self.amenities))

    def units_for(self, guest_count: int) -> int:
        if guest_count <= 0:
            raise DomainValidationError("Guest count must be greater than zero.")
        return (guest_count + self.guests_per_unit - 1) // self.guests_per_unit


@dataclass(frozen=True, slots=True)
class Property:
    id: UUID
    owner_id: UUID
    name: str
    city: str
    locality: str
    address: str
    star_rating: Decimal
    amenities: frozenset[str]
    room_types: tuple[RoomType, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        try:
            rating = (
                self.star_rating
                if isinstance(self.star_rating, Decimal)
                else Decimal(str(self.star_rating))
            )
        except (InvalidOperation, ValueError) as exc:
            raise DomainValidationError("Star rating must be numeric.") from exc
        if not rating.is_finite() or rating < Decimal("1") or rating > Decimal("5"):
            raise DomainValidationError("Star rating must be between 1 and 5.")
        if not self.room_types:
            raise DomainValidationError("A property must define at least one room type.")
        if any(room.property_id != self.id for room in self.room_types):
            raise DomainValidationError("Every room type must belong to its property.")
        room_ids = [room.id for room in self.room_types]
        if len(room_ids) != len(set(room_ids)):
            raise DomainValidationError("Room type identifiers must be unique within a property.")

        object.__setattr__(self, "name", _required_text(self.name, "Property name"))
        object.__setattr__(self, "city", _required_text(self.city, "City"))
        object.__setattr__(self, "locality", _required_text(self.locality, "Locality"))
        object.__setattr__(self, "address", _required_text(self.address, "Address"))
        object.__setattr__(self, "star_rating", rating)
        object.__setattr__(self, "amenities", normalize_amenities(self.amenities))
        object.__setattr__(self, "room_types", tuple(self.room_types))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))

    def find_room_type(self, room_type_id: UUID) -> RoomType | None:
        return next((room for room in self.room_types if room.id == room_type_id), None)


@dataclass(frozen=True, slots=True)
class CancellationRecord:
    cancelled_at: datetime
    refund_amount: Money
    refund_percentage: Decimal
    refund_status: RefundStatus

    def __post_init__(self) -> None:
        try:
            percentage = (
                self.refund_percentage
                if isinstance(self.refund_percentage, Decimal)
                else Decimal(str(self.refund_percentage))
            )
        except (InvalidOperation, ValueError) as exc:
            raise DomainValidationError("Refund percentage must be numeric.") from exc
        if percentage < 0 or percentage > 100:
            raise DomainValidationError("Refund percentage must be between 0 and 100.")
        object.__setattr__(self, "refund_percentage", percentage)
        object.__setattr__(self, "cancelled_at", _utc(self.cancelled_at, "cancelled_at"))


@dataclass(slots=True)
class Booking:
    id: UUID
    property_id: UUID
    room_type_id: UUID
    stay: StayPeriod
    guest_count: int
    required_units: int
    total_price: Money
    created_at: datetime
    updated_at: datetime
    status: BookingStatus = BookingStatus.PENDING_PAYMENT
    payment_id: UUID | None = None
    cancellation: CancellationRecord | None = None

    def __post_init__(self) -> None:
        if self.guest_count <= 0:
            raise DomainValidationError("Guest count must be greater than zero.")
        if self.required_units <= 0:
            raise DomainValidationError("Required room units must be greater than zero.")
        self.created_at = _utc(self.created_at, "created_at")
        self.updated_at = _utc(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise DomainValidationError("updated_at cannot be earlier than created_at.")
        if (
            self.status in {BookingStatus.CONFIRMED, BookingStatus.PAYMENT_FAILED}
            and self.payment_id is None
        ):
            raise DomainValidationError("A processed booking requires a payment identifier.")
        if self.status is BookingStatus.PENDING_PAYMENT and self.payment_id is not None:
            raise DomainValidationError("A pending booking cannot have a payment identifier.")
        if self.status is BookingStatus.CANCELLED and self.cancellation is None:
            raise DomainValidationError("A cancelled booking requires cancellation details.")
        if self.status is not BookingStatus.CANCELLED and self.cancellation is not None:
            raise DomainValidationError("Only a cancelled booking can hold cancellation details.")
        if self.cancellation is not None:
            self._validate_cancellation(self.cancellation)

    @property
    def reserves_inventory(self) -> bool:
        return self.status in {BookingStatus.PENDING_PAYMENT, BookingStatus.CONFIRMED}

    def confirm(self, payment_id: UUID, occurred_at: datetime) -> None:
        self._require_pending(BookingStatus.CONFIRMED)
        transition_time = self._validated_transition_time(occurred_at, "occurred_at")
        self.status = BookingStatus.CONFIRMED
        self.payment_id = payment_id
        self.updated_at = transition_time

    def mark_payment_failed(self, payment_id: UUID, occurred_at: datetime) -> None:
        self._require_pending(BookingStatus.PAYMENT_FAILED)
        transition_time = self._validated_transition_time(occurred_at, "occurred_at")
        self.status = BookingStatus.PAYMENT_FAILED
        self.payment_id = payment_id
        self.updated_at = transition_time

    def cancel(self, cancellation: CancellationRecord) -> CancellationRecord:
        if self.status is BookingStatus.CANCELLED:
            if self.cancellation is None:  # defensive guard for deserialized invalid state
                raise InvalidBookingTransitionError()
            return self.cancellation
        if self.status not in {BookingStatus.PENDING_PAYMENT, BookingStatus.CONFIRMED}:
            raise InvalidBookingTransitionError(
                details={"from": self.status.value, "to": BookingStatus.CANCELLED.value}
            )
        if self.status is BookingStatus.PENDING_PAYMENT:
            self._validate_pending_cancellation(cancellation)
        self._validate_cancellation(cancellation)
        self.status = BookingStatus.CANCELLED
        self.cancellation = cancellation
        self.updated_at = cancellation.cancelled_at
        return cancellation

    def _require_pending(self, target: BookingStatus) -> None:
        if self.status is not BookingStatus.PENDING_PAYMENT:
            raise InvalidBookingTransitionError(
                details={"from": self.status.value, "to": target.value}
            )

    def _validated_transition_time(self, occurred_at: datetime, field_name: str) -> datetime:
        transition_time = _utc(occurred_at, field_name)
        if transition_time < self.updated_at:
            raise DomainValidationError(
                f"{field_name} cannot be earlier than updated_at.",
                details={
                    field_name: transition_time.isoformat(),
                    "updated_at": self.updated_at.isoformat(),
                },
            )
        return transition_time

    def _validate_cancellation(self, cancellation: CancellationRecord) -> None:
        if cancellation.cancelled_at < self.updated_at:
            raise DomainValidationError(
                "cancelled_at cannot be earlier than updated_at.",
                details={
                    "cancelled_at": cancellation.cancelled_at.isoformat(),
                    "updated_at": self.updated_at.isoformat(),
                },
            )
        self.total_price.require_same_currency(cancellation.refund_amount)
        if cancellation.refund_amount.amount > self.total_price.amount:
            raise DomainValidationError("Refund amount cannot exceed the booking total price.")
        expected_amount = self.total_price.percentage(cancellation.refund_percentage)
        if cancellation.refund_amount != expected_amount:
            raise DomainValidationError(
                "Refund amount must match the recorded refund percentage.",
                details={
                    "expected_amount": format(expected_amount.amount, "f"),
                    "actual_amount": format(cancellation.refund_amount.amount, "f"),
                    "refund_percentage": format(cancellation.refund_percentage, "f"),
                },
            )
        expected_status = (
            RefundStatus.CALCULATED
            if cancellation.refund_percentage > 0
            else RefundStatus.NOT_REQUIRED
        )
        if cancellation.refund_status is not expected_status:
            raise DomainValidationError(
                "Refund status must match the recorded refund percentage.",
                details={
                    "expected_status": expected_status.value,
                    "actual_status": cancellation.refund_status.value,
                },
            )

    @staticmethod
    def _validate_pending_cancellation(cancellation: CancellationRecord) -> None:
        if (
            cancellation.refund_amount.amount != 0
            or cancellation.refund_percentage != 0
            or cancellation.refund_status is not RefundStatus.NOT_REQUIRED
        ):
            raise DomainValidationError(
                "A pending-payment cancellation must have a zero refund marked NOT_REQUIRED."
            )


@dataclass(frozen=True, slots=True)
class PaymentRecord:
    id: UUID
    booking_id: UUID
    method: PaymentMethod
    amount: Money
    status: PaymentStatus
    mock_outcome: MockPaymentOutcome
    provider_reference: str
    idempotency_key: str
    fingerprint: str
    booking_status_after: BookingStatus
    created_at: datetime

    def __post_init__(self) -> None:
        key = _required_text(self.idempotency_key, "Idempotency key")
        fingerprint = _required_text(self.fingerprint, "Payment fingerprint")
        reference = _required_text(self.provider_reference, "Provider reference")
        expected_payment = (
            PaymentStatus.APPROVED
            if self.mock_outcome is MockPaymentOutcome.APPROVED
            else PaymentStatus.REJECTED
        )
        expected_booking = (
            BookingStatus.CONFIRMED
            if self.status is PaymentStatus.APPROVED
            else BookingStatus.PAYMENT_FAILED
        )
        if self.status is not expected_payment or self.booking_status_after is not expected_booking:
            raise DomainValidationError(
                "Payment outcome, payment status, and booking status disagree."
            )
        object.__setattr__(self, "idempotency_key", key)
        object.__setattr__(self, "fingerprint", fingerprint)
        object.__setattr__(self, "provider_reference", reference)
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
