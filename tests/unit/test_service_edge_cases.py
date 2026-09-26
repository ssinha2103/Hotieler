from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID

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
from hotieler.application.services import PaymentService
from hotieler.container import AppContainer, build_container
from hotieler.domain.entities import Booking, Property
from hotieler.domain.enums import (
    BookingStatus,
    MockPaymentOutcome,
    PaymentMethod,
    PaymentStatus,
)
from hotieler.domain.errors import (
    CancellationNotAllowedError,
    DomainValidationError,
    IdempotencyConflictError,
    InvalidBookingTransitionError,
    ResourceNotFoundError,
)
from hotieler.domain.value_objects import Money, StayPeriod
from hotieler.infrastructure.clock import DeterministicIdGenerator, FixedClock

NOW = datetime(2030, 1, 1, 10, tzinfo=UTC)
STAY = StayPeriod(date(2030, 1, 10), date(2030, 1, 12))


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def container(clock: FixedClock) -> AppContainer:
    return build_container(
        clock=clock,
        ids=DeterministicIdGenerator(NAMESPACE_URL, prefix="service-edge-cases"),
    )


def onboard_property(
    container: AppContainer,
    *,
    total_units: int = 1,
    guests_per_unit: int = 2,
) -> Property:
    owner = container.catalog_service.create_owner(
        CreateOwnerCommand(name="Edge Case Hotels", contact_email="owner@edge.example")
    )
    return container.catalog_service.create_property(
        CreatePropertyCommand(
            owner_id=owner.id,
            name="Boundary Residency",
            city="Bengaluru",
            locality="Indiranagar",
            address="1 Test Avenue",
            star_rating=Decimal("4.5"),
            amenities=frozenset({"parking"}),
            room_types=(
                RoomTypeInput(
                    name="Deluxe",
                    total_units=total_units,
                    guests_per_unit=guests_per_unit,
                    nightly_rate=Money(Decimal("1000.00")),
                    amenities=frozenset({"wifi"}),
                ),
            ),
        )
    )


def create_booking(
    container: AppContainer,
    property_: Property,
    *,
    stay: StayPeriod = STAY,
    guest_count: int = 1,
) -> Booking:
    return container.booking_service.create(
        CreateBookingCommand(
            property_id=property_.id,
            room_type_id=property_.room_types[0].id,
            stay=stay,
            guest_count=guest_count,
        )
    )


@pytest.mark.parametrize(
    "query",
    [
        SearchQuery(city="   ", stay=STAY, guest_count=1),
        SearchQuery(city="Bengaluru", locality="   ", stay=STAY, guest_count=1),
        SearchQuery(city="Bengaluru", stay=STAY, guest_count=0),
        SearchQuery(
            city="Bengaluru",
            stay=STAY,
            guest_count=1,
            min_star_rating=Decimal("0"),
        ),
        SearchQuery(
            city="Bengaluru",
            stay=STAY,
            guest_count=1,
            min_star_rating=Decimal("6"),
        ),
        SearchQuery(
            city="Bengaluru",
            stay=STAY,
            guest_count=1,
            min_price=Money(Decimal("2000.00")),
            max_price=Money(Decimal("1000.00")),
        ),
        SearchQuery(
            city="Bengaluru",
            stay=STAY,
            guest_count=1,
            min_price=Money(Decimal("1000.00"), "USD"),
            max_price=Money(Decimal("2000.00"), "INR"),
        ),
        SearchQuery(
            city="Bengaluru",
            stay=StayPeriod(date(2029, 12, 30), date(2029, 12, 31)),
            guest_count=1,
        ),
    ],
    ids=(
        "blank-city",
        "blank-locality",
        "non-positive-guests",
        "star-rating-below-range",
        "star-rating-above-range",
        "minimum-price-above-maximum",
        "mismatched-price-currencies",
        "past-stay",
    ),
)
def test_search_rejects_invalid_business_queries(
    container: AppContainer,
    query: SearchQuery,
) -> None:
    with pytest.raises(DomainValidationError):
        container.availability_service.search(query)


def test_search_reports_partial_remaining_inventory(container: AppContainer) -> None:
    property_ = onboard_property(container, total_units=3, guests_per_unit=2)
    first = create_booking(container, property_, guest_count=3)

    one_unit_quotes = container.availability_service.search(
        SearchQuery(city="Bengaluru", stay=STAY, guest_count=1)
    )
    two_unit_quotes = container.availability_service.search(
        SearchQuery(city="Bengaluru", stay=STAY, guest_count=3)
    )

    assert first.required_units == 2
    assert len(one_unit_quotes) == 1
    assert one_unit_quotes[0].required_units == 1
    assert one_unit_quotes[0].available_units == 1
    assert two_unit_quotes == []

    second = create_booking(container, property_, guest_count=1)
    assert second.required_units == 1
    assert (
        container.availability_service.search(
            SearchQuery(city="Bengaluru", stay=STAY, guest_count=1)
        )
        == []
    )


def test_booking_service_allows_adjacent_and_disjoint_stays_on_one_unit(
    container: AppContainer,
) -> None:
    property_ = onboard_property(container, total_units=1)
    stays = (
        StayPeriod(date(2030, 1, 8), date(2030, 1, 10)),
        STAY,
        StayPeriod(date(2030, 1, 12), date(2030, 1, 14)),
        StayPeriod(date(2030, 1, 20), date(2030, 1, 21)),
    )

    bookings = [create_booking(container, property_, stay=stay) for stay in stays]

    assert [booking.stay for booking in bookings] == list(stays)
    assert len(container.booking_repository.list()) == len(stays)


def test_booking_service_reports_unknown_property_and_room_type(
    container: AppContainer,
) -> None:
    unknown_property_id = UUID(int=9001)
    with pytest.raises(ResourceNotFoundError) as missing_property:
        container.booking_service.create(
            CreateBookingCommand(
                property_id=unknown_property_id,
                room_type_id=UUID(int=9002),
                stay=STAY,
                guest_count=1,
            )
        )
    assert missing_property.value.details == {
        "resource": "Property",
        "id": str(unknown_property_id),
    }

    property_ = onboard_property(container)
    unknown_room_type_id = UUID(int=9003)
    with pytest.raises(ResourceNotFoundError) as missing_room:
        container.booking_service.create(
            CreateBookingCommand(
                property_id=property_.id,
                room_type_id=unknown_room_type_id,
                stay=STAY,
                guest_count=1,
            )
        )
    assert missing_room.value.details == {
        "resource": "Room type",
        "id": str(unknown_room_type_id),
    }


def test_booking_lookup_reports_unknown_booking(container: AppContainer) -> None:
    booking_id = UUID(int=9010)

    with pytest.raises(ResourceNotFoundError) as error:
        container.booking_service.get(booking_id)

    assert error.value.details == {"resource": "Booking", "id": str(booking_id)}


def test_payment_reports_unknown_booking_without_record(container: AppContainer) -> None:
    booking_id = UUID(int=9011)
    idempotency_key = "unknown-booking-payment"

    with pytest.raises(ResourceNotFoundError) as error:
        container.payment_service.process(
            ProcessPaymentCommand(
                booking_id=booking_id,
                method=PaymentMethod.CARD,
                mock_outcome=MockPaymentOutcome.APPROVED,
                idempotency_key=idempotency_key,
            )
        )

    assert error.value.details == {"resource": "Booking", "id": str(booking_id)}
    assert container.payment_repository.get_by_idempotency_key(idempotency_key) is None


def test_idempotency_key_cannot_be_reused_for_another_booking(
    container: AppContainer,
) -> None:
    property_ = onboard_property(container, total_units=2)
    first = create_booking(container, property_)
    second = create_booking(container, property_)
    key = "booking-bound-idempotency-key"
    first_result = container.payment_service.process(
        ProcessPaymentCommand(
            booking_id=first.id,
            method=PaymentMethod.CARD,
            mock_outcome=MockPaymentOutcome.APPROVED,
            idempotency_key=key,
        )
    )

    with pytest.raises(IdempotencyConflictError):
        container.payment_service.process(
            ProcessPaymentCommand(
                booking_id=second.id,
                method=PaymentMethod.CARD,
                mock_outcome=MockPaymentOutcome.APPROVED,
                idempotency_key=key,
            )
        )

    assert first_result.payment.booking_id == first.id
    assert container.booking_service.get(second.id).status is BookingStatus.PENDING_PAYMENT
    assert container.payment_repository.get_by_idempotency_key(key) == first_result.payment


class InconsistentPaymentProcessor:
    def __init__(self, processed_at: datetime) -> None:
        self._processed_at = processed_at
        self.calls = 0

    def process(
        self,
        booking_id: UUID,
        amount: Money,
        mock_outcome: MockPaymentOutcome,
    ) -> PaymentProcessorResult:
        del booking_id, amount, mock_outcome
        self.calls += 1
        return PaymentProcessorResult(
            status=PaymentStatus.REJECTED,
            provider_reference="inconsistent-processor-result",
            processed_at=self._processed_at,
        )


def test_payment_rejects_inconsistent_processor_result_without_mutating_booking(
    container: AppContainer,
) -> None:
    property_ = onboard_property(container)
    booking = create_booking(container, property_)
    processor = InconsistentPaymentProcessor(NOW)
    service = PaymentService(
        bookings=container.booking_repository,
        payments=container.payment_repository,
        processors={PaymentMethod.CARD: processor},
        clock=FixedClock(NOW),
        ids=DeterministicIdGenerator(NAMESPACE_URL, prefix="inconsistent-payment"),
        locks=container.lock_manager,
    )
    idempotency_key = "inconsistent-outcome"

    with pytest.raises(DomainValidationError):
        service.process(
            ProcessPaymentCommand(
                booking_id=booking.id,
                method=PaymentMethod.CARD,
                mock_outcome=MockPaymentOutcome.APPROVED,
                idempotency_key=idempotency_key,
            )
        )

    stored = container.booking_service.get(booking.id)
    assert processor.calls == 1
    assert stored.status is BookingStatus.PENDING_PAYMENT
    assert stored.payment_id is None
    assert container.payment_repository.get_by_idempotency_key(idempotency_key) is None


@pytest.mark.parametrize(
    "terminal_status",
    [
        BookingStatus.CONFIRMED,
        BookingStatus.PAYMENT_FAILED,
        BookingStatus.CANCELLED,
    ],
)
def test_terminal_booking_states_reject_payment_with_a_new_key(
    container: AppContainer,
    terminal_status: BookingStatus,
) -> None:
    property_ = onboard_property(container)
    booking = create_booking(container, property_)
    if terminal_status is BookingStatus.CONFIRMED:
        container.payment_service.process(
            ProcessPaymentCommand(
                booking_id=booking.id,
                method=PaymentMethod.CARD,
                mock_outcome=MockPaymentOutcome.APPROVED,
                idempotency_key="terminal-setup-confirmed",
            )
        )
    elif terminal_status is BookingStatus.PAYMENT_FAILED:
        container.payment_service.process(
            ProcessPaymentCommand(
                booking_id=booking.id,
                method=PaymentMethod.CARD,
                mock_outcome=MockPaymentOutcome.REJECTED,
                idempotency_key="terminal-setup-payment-failed",
            )
        )
    else:
        container.cancellation_service.cancel(booking.id)

    before = container.booking_service.get(booking.id)
    new_key = f"new-payment-{terminal_status.value.casefold()}"
    with pytest.raises(InvalidBookingTransitionError):
        container.payment_service.process(
            ProcessPaymentCommand(
                booking_id=booking.id,
                method=PaymentMethod.WALLET,
                mock_outcome=MockPaymentOutcome.APPROVED,
                idempotency_key=new_key,
            )
        )

    assert container.booking_service.get(booking.id) == before
    assert container.payment_repository.get_by_idempotency_key(new_key) is None


def test_cancellation_reports_unknown_booking(container: AppContainer) -> None:
    booking_id = UUID(int=9020)

    with pytest.raises(ResourceNotFoundError) as error:
        container.cancellation_service.cancel(booking_id)

    assert error.value.details == {"resource": "Booking", "id": str(booking_id)}


def test_cancellation_rejects_payment_failed_booking_without_mutation(
    container: AppContainer,
) -> None:
    property_ = onboard_property(container)
    booking = create_booking(container, property_)
    container.payment_service.process(
        ProcessPaymentCommand(
            booking_id=booking.id,
            method=PaymentMethod.UPI,
            mock_outcome=MockPaymentOutcome.REJECTED,
            idempotency_key="failed-before-cancellation",
        )
    )
    before = container.booking_service.get(booking.id)

    with pytest.raises(CancellationNotAllowedError):
        container.cancellation_service.cancel(booking.id)

    assert container.booking_service.get(booking.id) == before


def test_cancellation_rejects_booking_after_check_in_without_mutation(
    container: AppContainer,
    clock: FixedClock,
) -> None:
    property_ = onboard_property(container)
    booking = create_booking(
        container,
        property_,
        stay=StayPeriod(date(2030, 1, 2), date(2030, 1, 4)),
    )
    container.payment_service.process(
        ProcessPaymentCommand(
            booking_id=booking.id,
            method=PaymentMethod.CARD,
            mock_outcome=MockPaymentOutcome.APPROVED,
            idempotency_key="confirmed-before-check-in",
        )
    )
    clock.set(datetime(2030, 1, 3, 10, tzinfo=UTC))
    before = container.booking_service.get(booking.id)

    with pytest.raises(CancellationNotAllowedError):
        container.cancellation_service.cancel(booking.id)

    assert container.booking_service.get(booking.id) == before
