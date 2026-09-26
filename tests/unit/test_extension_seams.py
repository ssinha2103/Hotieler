from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from hotieler.application.models import (
    CreateBookingCommand,
    CreateOwnerCommand,
    CreatePropertyCommand,
    PaymentProcessorResult,
    ProcessPaymentCommand,
    RoomTypeInput,
    SearchQuery,
)
from hotieler.application.services import (
    AvailabilitySearchService,
    BookingService,
    CancellationService,
    CatalogService,
    PaymentService,
)
from hotieler.domain.entities import Booking, Property
from hotieler.domain.enums import (
    BookingStatus,
    MockPaymentOutcome,
    PaymentMethod,
    PaymentStatus,
    RefundStatus,
)
from hotieler.domain.errors import DomainValidationError, UnsupportedPaymentMethodError
from hotieler.domain.policies import (
    CancellationPolicy,
    PricingStrategy,
    RefundQuote,
    StandardPricingStrategy,
)
from hotieler.domain.value_objects import Money, StayPeriod
from tests.unit.fakes import (
    FakeBookingRepository,
    FakeOwnerRepository,
    FakePaymentRepository,
    FakePropertyRepository,
    FixedClock,
    IncrementingIdGenerator,
    ThreadSafeKeyedLockManager,
)

NOW = datetime(2030, 1, 1, 10, tzinfo=UTC)
STAY = StayPeriod(date(2030, 1, 10), date(2030, 1, 12))


@dataclass(slots=True)
class FixedQuotePricingStrategy:
    quote: Money
    calls: list[tuple[StayPeriod, int, Money]] = field(default_factory=list)

    def calculate(self, stay: StayPeriod, required_units: int, nightly_rate: Money) -> Money:
        self.calls.append((stay, required_units, nightly_rate))
        return self.quote


@dataclass(slots=True)
class RecordingPaymentProcessor:
    processed_at: datetime
    provider_reference: str = "custom-provider-reference"
    calls: list[tuple[UUID, Money, MockPaymentOutcome]] = field(default_factory=list)

    def process(
        self,
        booking_id: UUID,
        amount: Money,
        mock_outcome: MockPaymentOutcome,
    ) -> PaymentProcessorResult:
        self.calls.append((booking_id, amount, mock_outcome))
        return PaymentProcessorResult(
            status=(
                PaymentStatus.APPROVED
                if mock_outcome is MockPaymentOutcome.APPROVED
                else PaymentStatus.REJECTED
            ),
            provider_reference=self.provider_reference,
            processed_at=self.processed_at,
        )


@dataclass(slots=True)
class QuarterRefundPolicy:
    calls: list[tuple[UUID, date]] = field(default_factory=list)

    def quote(self, booking: Booking, on_date: date) -> RefundQuote:
        self.calls.append((booking.id, on_date))
        return RefundQuote(
            amount=booking.total_price.percentage(Decimal("25")),
            percentage=Decimal("25"),
            status=RefundStatus.CALCULATED,
        )


class InconsistentRefundPolicy:
    def quote(self, booking: Booking, on_date: date) -> RefundQuote:
        del on_date
        return RefundQuote(
            amount=Money.zero(booking.total_price.currency),
            percentage=Decimal("100"),
            status=RefundStatus.NOT_REQUIRED,
        )


@dataclass(slots=True)
class ServiceHarness:
    property: Property
    bookings: FakeBookingRepository
    payments: FakePaymentRepository
    clock: FixedClock
    ids: IncrementingIdGenerator
    locks: ThreadSafeKeyedLockManager
    availability: AvailabilitySearchService
    booking: BookingService


def build_harness(pricing: PricingStrategy | None = None) -> ServiceHarness:
    owners = FakeOwnerRepository()
    properties = FakePropertyRepository()
    bookings = FakeBookingRepository()
    payments = FakePaymentRepository()
    clock = FixedClock(NOW)
    ids = IncrementingIdGenerator()
    locks = ThreadSafeKeyedLockManager()
    selected_pricing = pricing or StandardPricingStrategy()
    catalog = CatalogService(owners, properties, ids, clock)
    owner = catalog.create_owner(
        CreateOwnerCommand(name="Extension Hotels", contact_email="owner@example.com")
    )
    property_ = catalog.create_property(
        CreatePropertyCommand(
            owner_id=owner.id,
            name="Pluggable Stay",
            city="Bengaluru",
            locality="Indiranagar",
            address="1 Extension Road",
            star_rating=Decimal("4.5"),
            amenities=frozenset({"parking"}),
            room_types=(
                RoomTypeInput(
                    name="Deluxe",
                    total_units=2,
                    guests_per_unit=2,
                    nightly_rate=Money(Decimal("500")),
                    amenities=frozenset({"wifi"}),
                ),
            ),
        )
    )
    return ServiceHarness(
        property=property_,
        bookings=bookings,
        payments=payments,
        clock=clock,
        ids=ids,
        locks=locks,
        availability=AvailabilitySearchService(properties, bookings, selected_pricing, clock),
        booking=BookingService(properties, bookings, selected_pricing, clock, ids, locks),
    )


def create_booking(harness: ServiceHarness) -> Booking:
    return harness.booking.create(
        CreateBookingCommand(
            property_id=harness.property.id,
            room_type_id=harness.property.room_types[0].id,
            stay=STAY,
            guest_count=1,
        )
    )


def payment_service(
    harness: ServiceHarness,
    processors: dict[PaymentMethod, RecordingPaymentProcessor],
) -> PaymentService:
    return PaymentService(
        bookings=harness.bookings,
        payments=harness.payments,
        processors=processors,
        clock=harness.clock,
        ids=harness.ids,
        locks=harness.locks,
    )


def test_alternate_pricing_strategy_plugs_into_search_and_booking() -> None:
    pricing = FixedQuotePricingStrategy(Money(Decimal("777.77")))
    harness = build_harness(pricing)

    quotes = harness.availability.search(SearchQuery(city="Bengaluru", stay=STAY, guest_count=1))
    booking = create_booking(harness)

    assert quotes[0].total_price == pricing.quote
    assert booking.total_price == pricing.quote
    assert pricing.calls == [
        (STAY, 1, Money(Decimal("500"))),
        (STAY, 1, Money(Decimal("500"))),
    ]


def test_alternate_cancellation_policy_plugs_into_service() -> None:
    harness = build_harness()
    booking = create_booking(harness)
    processor = RecordingPaymentProcessor(NOW)
    payment_service(harness, {PaymentMethod.CARD: processor}).process(
        ProcessPaymentCommand(
            booking_id=booking.id,
            method=PaymentMethod.CARD,
            mock_outcome=MockPaymentOutcome.APPROVED,
            idempotency_key="confirm-before-custom-cancellation",
        )
    )
    policy: CancellationPolicy = QuarterRefundPolicy()

    cancelled = CancellationService(
        harness.bookings,
        policy,
        harness.clock,
        harness.locks,
    ).cancel(booking.id)

    assert cancelled.status is BookingStatus.CANCELLED
    assert cancelled.cancellation is not None
    assert cancelled.cancellation.refund_percentage == Decimal("25")
    assert cancelled.cancellation.refund_amount == Money(Decimal("250"))
    assert isinstance(policy, QuarterRefundPolicy)
    assert policy.calls == [(booking.id, NOW.date())]


def test_invalid_cancellation_policy_result_cannot_corrupt_booking_state() -> None:
    harness = build_harness()
    booking = create_booking(harness)
    processor = RecordingPaymentProcessor(NOW)
    payment_service(harness, {PaymentMethod.CARD: processor}).process(
        ProcessPaymentCommand(
            booking_id=booking.id,
            method=PaymentMethod.CARD,
            mock_outcome=MockPaymentOutcome.APPROVED,
            idempotency_key="confirm-before-invalid-cancellation",
        )
    )

    with pytest.raises(DomainValidationError, match="amount must match"):
        CancellationService(
            harness.bookings,
            InconsistentRefundPolicy(),
            harness.clock,
            harness.locks,
        ).cancel(booking.id)

    persisted = harness.booking.get(booking.id)
    assert persisted.status is BookingStatus.CONFIRMED
    assert persisted.cancellation is None


def test_custom_registered_payment_processor_plugs_into_service() -> None:
    harness = build_harness()
    booking = create_booking(harness)
    processor = RecordingPaymentProcessor(NOW, provider_reference="wallet-adapter-42")

    result = payment_service(harness, {PaymentMethod.WALLET: processor}).process(
        ProcessPaymentCommand(
            booking_id=booking.id,
            method=PaymentMethod.WALLET,
            mock_outcome=MockPaymentOutcome.APPROVED,
            idempotency_key="custom-wallet-processor",
        )
    )

    assert result.booking.status is BookingStatus.CONFIRMED
    assert result.payment.provider_reference == "wallet-adapter-42"
    assert processor.calls == [(booking.id, booking.total_price, MockPaymentOutcome.APPROVED)]


def test_unregistered_payment_processor_is_rejected_without_mutation() -> None:
    harness = build_harness()
    booking = create_booking(harness)
    service = payment_service(harness, {})

    with pytest.raises(UnsupportedPaymentMethodError) as error:
        service.process(
            ProcessPaymentCommand(
                booking_id=booking.id,
                method=PaymentMethod.UPI,
                mock_outcome=MockPaymentOutcome.APPROVED,
                idempotency_key="unregistered-upi-processor",
            )
        )

    assert error.value.details == {"method": PaymentMethod.UPI.value}
    assert harness.booking.get(booking.id).status is BookingStatus.PENDING_PAYMENT
    assert harness.payments.get_by_idempotency_key("unregistered-upi-processor") is None
