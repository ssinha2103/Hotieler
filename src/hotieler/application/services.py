"""Application services coordinating domain behavior through ports."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

from hotieler.application.models import (
    AvailabilityQuote,
    CreateBookingCommand,
    CreateOwnerCommand,
    CreatePropertyCommand,
    PaymentCommandResult,
    ProcessPaymentCommand,
    SearchQuery,
)
from hotieler.application.ports import (
    BookingRepository,
    Clock,
    IdGenerator,
    KeyedLockManager,
    OwnerRepository,
    PaymentProcessor,
    PaymentRepository,
    PropertyRepository,
)
from hotieler.domain.entities import (
    Booking,
    CancellationRecord,
    OwnerAccount,
    PaymentRecord,
    Property,
    RoomType,
)
from hotieler.domain.enums import BookingStatus, MockPaymentOutcome, PaymentMethod, PaymentStatus
from hotieler.domain.errors import (
    DomainValidationError,
    IdempotencyConflictError,
    InvalidBookingTransitionError,
    PropertyRoomMismatchError,
    ResourceNotFoundError,
    RoomInventoryUnavailableError,
    UnsupportedPaymentMethodError,
)
from hotieler.domain.policies import CancellationPolicy, PricingStrategy
from hotieler.domain.specifications import (
    AmenitiesSpecification,
    AndSpecification,
    CitySpecification,
    LocalitySpecification,
    MinStarRatingSpecification,
    NightlyRateSpecification,
    PropertyRoomCandidate,
    Specification,
)
from hotieler.domain.value_objects import StayPeriod

logger = logging.getLogger("hotieler.application")


def _not_found(resource: str, identifier: UUID) -> ResourceNotFoundError:
    return ResourceNotFoundError(
        f"{resource} was not found.",
        details={"resource": resource, "id": str(identifier)},
    )


def _reserved_units(bookings: list[Booking], room_type_id: UUID, stay: StayPeriod) -> int:
    return sum(
        booking.required_units
        for booking in bookings
        if booking.room_type_id == room_type_id
        and booking.reserves_inventory
        and booking.stay.overlaps(stay)
    )


class CatalogService:
    """Owner and property onboarding use cases."""

    def __init__(
        self,
        owners: OwnerRepository,
        properties: PropertyRepository,
        ids: IdGenerator,
        clock: Clock,
    ) -> None:
        self._owners = owners
        self._properties = properties
        self._ids = ids
        self._clock = clock

    def create_owner(self, command: CreateOwnerCommand) -> OwnerAccount:
        owner = OwnerAccount(
            id=self._ids.new(),
            name=command.name,
            contact_email=command.contact_email,
            created_at=self._clock.now(),
        )
        self._owners.add(owner)
        logger.info("owner_created", extra={"owner_id": str(owner.id)})
        return owner

    def create_property(self, command: CreatePropertyCommand) -> Property:
        if self._owners.get(command.owner_id) is None:
            raise _not_found("Owner", command.owner_id)
        property_id = self._ids.new()
        room_types = tuple(
            RoomType(
                id=self._ids.new(),
                property_id=property_id,
                name=room.name,
                total_units=room.total_units,
                guests_per_unit=room.guests_per_unit,
                nightly_rate=room.nightly_rate,
                amenities=room.amenities,
            )
            for room in command.room_types
        )
        property = Property(
            id=property_id,
            owner_id=command.owner_id,
            name=command.name,
            city=command.city,
            locality=command.locality,
            address=command.address,
            star_rating=command.star_rating,
            amenities=command.amenities,
            room_types=room_types,
            created_at=self._clock.now(),
        )
        self._properties.add(property)
        logger.info(
            "property_created",
            extra={
                "owner_id": str(property.owner_id),
                "property_id": str(property.id),
            },
        )
        return property


class AvailabilitySearchService:
    """Advisory availability search over derived inventory."""

    def __init__(
        self,
        properties: PropertyRepository,
        bookings: BookingRepository,
        pricing: PricingStrategy,
        clock: Clock,
    ) -> None:
        self._properties = properties
        self._bookings = bookings
        self._pricing = pricing
        self._clock = clock

    def search(self, query: SearchQuery) -> list[AvailabilityQuote]:
        self._validate_query(query)
        specification = self._build_specification(query)
        bookings = self._bookings.list()
        results: list[AvailabilityQuote] = []
        for property in self._properties.list_properties():
            for room_type in property.room_types:
                candidate = PropertyRoomCandidate(property=property, room_type=room_type)
                if not specification.is_satisfied_by(candidate):
                    continue
                required_units = room_type.units_for(query.guest_count)
                reserved = _reserved_units(bookings, room_type.id, query.stay)
                available = room_type.total_units - reserved
                if available < required_units:
                    continue
                results.append(
                    AvailabilityQuote(
                        property=property,
                        room_type=room_type,
                        required_units=required_units,
                        available_units=available,
                        total_price=self._pricing.calculate(
                            query.stay, required_units, room_type.nightly_rate
                        ),
                    )
                )
        return sorted(
            results,
            key=lambda quote: (
                quote.total_price.currency,
                quote.total_price.amount,
                quote.property.name.casefold(),
                quote.room_type.name.casefold(),
                str(quote.room_type.id),
            ),
        )

    def _validate_query(self, query: SearchQuery) -> None:
        query.stay.ensure_not_in_past(self._clock.now().date())
        if not query.city.strip():
            raise DomainValidationError("City cannot be blank.")
        if query.locality is not None and not query.locality.strip():
            raise DomainValidationError("Locality cannot be blank when supplied.")
        if query.guest_count <= 0:
            raise DomainValidationError("Guest count must be greater than zero.")
        if query.min_star_rating is not None and not (
            Decimal("1") <= query.min_star_rating <= Decimal("5")
        ):
            raise DomainValidationError("Minimum star rating must be between 1 and 5.")
        if query.min_price is not None and query.max_price is not None:
            query.min_price.require_same_currency(query.max_price)
            if query.min_price.amount > query.max_price.amount:
                raise DomainValidationError("Minimum price cannot exceed maximum price.")

    @staticmethod
    def _build_specification(query: SearchQuery) -> Specification[PropertyRoomCandidate]:
        specifications: list[Specification[PropertyRoomCandidate]] = [
            CitySpecification(query.city),
            NightlyRateSpecification(query.min_price, query.max_price),
        ]
        if query.locality is not None:
            specifications.append(LocalitySpecification(query.locality))
        if query.amenities:
            specifications.append(AmenitiesSpecification(query.amenities))
        if query.min_star_rating is not None:
            specifications.append(MinStarRatingSpecification(query.min_star_rating))
        return AndSpecification(tuple(specifications))


class BookingService:
    """Authoritative booking creation and lookup."""

    def __init__(
        self,
        properties: PropertyRepository,
        bookings: BookingRepository,
        pricing: PricingStrategy,
        clock: Clock,
        ids: IdGenerator,
        locks: KeyedLockManager,
    ) -> None:
        self._properties = properties
        self._bookings = bookings
        self._pricing = pricing
        self._clock = clock
        self._ids = ids
        self._locks = locks

    def create(self, command: CreateBookingCommand) -> Booking:
        command.stay.ensure_not_in_past(self._clock.now().date())
        if command.guest_count <= 0:
            raise DomainValidationError("Guest count must be greater than zero.")
        property = self._properties.get(command.property_id)
        if property is None:
            raise _not_found("Property", command.property_id)
        room_type = self._properties.get_room_type(command.room_type_id)
        if room_type is None:
            raise _not_found("Room type", command.room_type_id)
        if room_type.property_id != property.id or property.find_room_type(room_type.id) is None:
            raise PropertyRoomMismatchError(
                details={
                    "property_id": str(command.property_id),
                    "room_type_id": str(command.room_type_id),
                }
            )

        with self._locks.lock(f"room_type:{room_type.id}"):
            required_units = room_type.units_for(command.guest_count)
            reserved = _reserved_units(self._bookings.list(), room_type.id, command.stay)
            available = room_type.total_units - reserved
            if available < required_units:
                logger.warning(
                    "booking_inventory_conflict",
                    extra={
                        "property_id": str(property.id),
                        "room_type_id": str(room_type.id),
                        "required_units": required_units,
                        "available_units": max(available, 0),
                    },
                )
                raise RoomInventoryUnavailableError(
                    details={
                        "room_type_id": str(room_type.id),
                        "required_units": required_units,
                        "available_units": max(available, 0),
                    }
                )
            now = self._clock.now()
            booking = Booking(
                id=self._ids.new(),
                property_id=property.id,
                room_type_id=room_type.id,
                stay=command.stay,
                guest_count=command.guest_count,
                required_units=required_units,
                total_price=self._pricing.calculate(
                    command.stay, required_units, room_type.nightly_rate
                ),
                created_at=now,
                updated_at=now,
            )
            self._bookings.save(booking)
            logger.info(
                "booking_inventory_held",
                extra={
                    "booking_id": str(booking.id),
                    "property_id": str(booking.property_id),
                    "room_type_id": str(booking.room_type_id),
                    "booking_status": booking.status.value,
                    "required_units": booking.required_units,
                },
            )
            return booking

    def get(self, booking_id: UUID) -> Booking:
        booking = self._bookings.get(booking_id)
        if booking is None:
            raise _not_found("Booking", booking_id)
        return booking


class PaymentService:
    """Mock payment processing with serializable idempotency semantics."""

    def __init__(
        self,
        bookings: BookingRepository,
        payments: PaymentRepository,
        processors: Mapping[PaymentMethod, PaymentProcessor],
        clock: Clock,
        ids: IdGenerator,
        locks: KeyedLockManager,
    ) -> None:
        self._bookings = bookings
        self._payments = payments
        self._processors = dict(processors)
        self._clock = clock
        self._ids = ids
        self._locks = locks

    def process(self, command: ProcessPaymentCommand) -> PaymentCommandResult:
        idempotency_key = command.idempotency_key.strip()
        if not idempotency_key:
            raise DomainValidationError("Idempotency-Key is required.")
        fingerprint = self.fingerprint(command)

        # Lock order is a public invariant: idempotency key before booking.
        with self._locks.lock(f"idempotency:{idempotency_key}"):
            replay = self._replay_if_present(idempotency_key, fingerprint)
            if replay is not None:
                return replay

            with self._locks.lock(f"booking:{command.booking_id}"):
                return self._process_new_payment(
                    command=command,
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                )

    def _replay_if_present(
        self,
        idempotency_key: str,
        fingerprint: str,
    ) -> PaymentCommandResult | None:
        existing = self._payments.get_by_idempotency_key(idempotency_key)
        if existing is None:
            return None
        if existing.fingerprint != fingerprint:
            logger.warning(
                "payment_idempotency_conflict",
                extra={
                    "booking_id": str(existing.booking_id),
                    "payment_id": str(existing.id),
                },
            )
            raise IdempotencyConflictError(details={"idempotency_key": idempotency_key})
        current_booking = self._get_booking(existing.booking_id)
        logger.info(
            "payment_replayed",
            extra={
                "booking_id": str(existing.booking_id),
                "payment_id": str(existing.id),
                "booking_status": existing.booking_status_after.value,
                "payment_status": existing.status.value,
                "payment_method": existing.method.value,
                "replayed": True,
            },
        )
        return PaymentCommandResult(
            booking=self._booking_at_payment(current_booking, existing),
            payment=existing,
            replayed=True,
        )

    def _process_new_payment(
        self,
        command: ProcessPaymentCommand,
        idempotency_key: str,
        fingerprint: str,
    ) -> PaymentCommandResult:
        booking = self._get_booking(command.booking_id)
        self._ensure_payment_is_allowed(booking)
        processor = self._processor_for(command.method)
        processor_result = processor.process(
            booking.id,
            booking.total_price,
            command.mock_outcome,
        )
        self._ensure_result_matches_outcome(processor_result.status, command.mock_outcome)
        payment = self._new_payment_record(
            booking=booking,
            command=command,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            status=processor_result.status,
            provider_reference=processor_result.provider_reference,
            processed_at=processor_result.processed_at,
        )
        self._apply_payment(booking, payment)
        self._bookings.save(booking)
        self._payments.save(payment)
        logger.info(
            "payment_processed",
            extra={
                "booking_id": str(booking.id),
                "payment_id": str(payment.id),
                "booking_status": booking.status.value,
                "payment_status": payment.status.value,
                "payment_method": payment.method.value,
                "replayed": False,
            },
        )
        return PaymentCommandResult(booking=booking, payment=payment, replayed=False)

    @staticmethod
    def _ensure_payment_is_allowed(booking: Booking) -> None:
        if booking.status is not BookingStatus.PENDING_PAYMENT:
            raise InvalidBookingTransitionError(
                details={
                    "from": booking.status.value,
                    "to": "PAYMENT_PROCESSED",
                }
            )

    def _processor_for(self, method: PaymentMethod) -> PaymentProcessor:
        processor = self._processors.get(method)
        if processor is None:
            raise UnsupportedPaymentMethodError(details={"method": method.value})
        return processor

    @staticmethod
    def _ensure_result_matches_outcome(
        status: PaymentStatus,
        outcome: MockPaymentOutcome,
    ) -> None:
        expected_status = (
            PaymentStatus.APPROVED
            if outcome is MockPaymentOutcome.APPROVED
            else PaymentStatus.REJECTED
        )
        if status is not expected_status:
            raise DomainValidationError(
                "Payment processor returned a result inconsistent with the requested mock outcome."
            )

    def _new_payment_record(
        self,
        *,
        booking: Booking,
        command: ProcessPaymentCommand,
        idempotency_key: str,
        fingerprint: str,
        status: PaymentStatus,
        provider_reference: str,
        processed_at: datetime,
    ) -> PaymentRecord:
        booking_status = (
            BookingStatus.CONFIRMED
            if status is PaymentStatus.APPROVED
            else BookingStatus.PAYMENT_FAILED
        )
        return PaymentRecord(
            id=self._ids.new(),
            booking_id=booking.id,
            method=command.method,
            amount=booking.total_price,
            status=status,
            mock_outcome=command.mock_outcome,
            provider_reference=provider_reference,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            booking_status_after=booking_status,
            created_at=processed_at,
        )

    @staticmethod
    def _apply_payment(booking: Booking, payment: PaymentRecord) -> None:
        if payment.status is PaymentStatus.APPROVED:
            booking.confirm(payment.id, payment.created_at)
        else:
            booking.mark_payment_failed(payment.id, payment.created_at)

    @staticmethod
    def fingerprint(command: ProcessPaymentCommand) -> str:
        raw = "|".join((str(command.booking_id), command.method.value, command.mock_outcome.value))
        return sha256(raw.encode("utf-8")).hexdigest()

    def _get_booking(self, booking_id: UUID) -> Booking:
        booking = self._bookings.get(booking_id)
        if booking is None:
            raise _not_found("Booking", booking_id)
        return booking

    @staticmethod
    def _booking_at_payment(booking: Booking, payment: PaymentRecord) -> Booking:
        """Rebuild the original response even if the booking changed later."""

        return Booking(
            id=booking.id,
            property_id=booking.property_id,
            room_type_id=booking.room_type_id,
            stay=booking.stay,
            guest_count=booking.guest_count,
            required_units=booking.required_units,
            total_price=booking.total_price,
            created_at=booking.created_at,
            updated_at=payment.created_at,
            status=payment.booking_status_after,
            payment_id=payment.id,
        )


class CancellationService:
    """Repeat-safe cancellation coordinated by the booking lock."""

    def __init__(
        self,
        bookings: BookingRepository,
        policy: CancellationPolicy,
        clock: Clock,
        locks: KeyedLockManager,
    ) -> None:
        self._bookings = bookings
        self._policy = policy
        self._clock = clock
        self._locks = locks

    def cancel(self, booking_id: UUID) -> Booking:
        with self._locks.lock(f"booking:{booking_id}"):
            booking = self._bookings.get(booking_id)
            if booking is None:
                raise _not_found("Booking", booking_id)
            if booking.status is BookingStatus.CANCELLED:
                cancellation = booking.cancellation
                logger.info(
                    "cancellation_replayed",
                    extra={
                        "booking_id": str(booking.id),
                        "booking_status": booking.status.value,
                        "refund_status": (
                            cancellation.refund_status.value
                            if cancellation is not None
                            else "UNKNOWN"
                        ),
                        "replayed": True,
                    },
                )
                return booking
            now = self._clock.now()
            quote = self._policy.quote(booking, now.date())
            booking.cancel(
                CancellationRecord(
                    cancelled_at=now,
                    refund_amount=quote.amount,
                    refund_percentage=quote.percentage,
                    refund_status=quote.status,
                )
            )
            self._bookings.save(booking)
            logger.info(
                "booking_cancelled",
                extra={
                    "booking_id": str(booking.id),
                    "booking_status": booking.status.value,
                    "refund_status": quote.status.value,
                    "replayed": False,
                },
            )
            return booking
