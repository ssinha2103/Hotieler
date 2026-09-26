from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from threading import Barrier
from uuid import NAMESPACE_OID, uuid4

from hotieler.application.models import (
    CreateBookingCommand,
    CreateOwnerCommand,
    CreatePropertyCommand,
    PaymentCommandResult,
    ProcessPaymentCommand,
    RoomTypeInput,
)
from hotieler.container import AppContainer, build_container
from hotieler.domain.entities import Booking
from hotieler.domain.enums import BookingStatus, MockPaymentOutcome, PaymentMethod
from hotieler.domain.errors import ConflictError, RoomInventoryUnavailableError
from hotieler.domain.value_objects import Money, StayPeriod
from hotieler.infrastructure.clock import DeterministicIdGenerator, FixedClock


def _container_with_one_room() -> tuple[AppContainer, CreateBookingCommand]:
    container = build_container(
        clock=FixedClock(datetime(2026, 1, 10, 12, 0, tzinfo=UTC)),
        ids=DeterministicIdGenerator(NAMESPACE_OID, prefix=str(uuid4())),
    )
    owner = container.catalog_service.create_owner(
        CreateOwnerCommand(name="Concurrency Hotels", contact_email="race@example.com")
    )
    property_ = container.catalog_service.create_property(
        CreatePropertyCommand(
            owner_id=owner.id,
            name="One Room Inn",
            city="Bengaluru",
            locality="Koramangala",
            address="Concurrency Street",
            star_rating=Decimal("4"),
            amenities=frozenset({"wifi"}),
            room_types=(
                RoomTypeInput(
                    name="Only Room",
                    total_units=1,
                    guests_per_unit=2,
                    nightly_rate=Money("1000.00", "INR"),
                    amenities=frozenset(),
                ),
            ),
        )
    )
    room_type = property_.room_types[0]
    return container, CreateBookingCommand(
        property_id=property_.id,
        room_type_id=room_type.id,
        stay=StayPeriod(date(2026, 1, 15), date(2026, 1, 16)),
        guest_count=2,
    )


def test_simultaneous_booking_attempts_have_exactly_one_winner() -> None:
    container, command = _container_with_one_room()
    participants = 12
    barrier = Barrier(participants)

    def attempt() -> Booking | RoomInventoryUnavailableError:
        barrier.wait()
        try:
            return container.booking_service.create(command)
        except RoomInventoryUnavailableError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=participants) as executor:
        results = list(executor.map(lambda _index: attempt(), range(participants)))

    winners = [result for result in results if isinstance(result, Booking)]
    conflicts = [result for result in results if isinstance(result, RoomInventoryUnavailableError)]
    assert len(winners) == 1
    assert len(conflicts) == participants - 1
    assert (
        sum(
            booking.required_units
            for booking in container.booking_repository.list()
            if booking.reserves_inventory
        )
        == 1
    )
    assert container.lock_manager.active_key_count == 0


def test_concurrent_same_key_payment_is_processed_once_and_replayed() -> None:
    container, booking_command = _container_with_one_room()
    booking = container.booking_service.create(booking_command)
    participants = 10
    barrier = Barrier(participants)
    payment_command = ProcessPaymentCommand(
        booking_id=booking.id,
        method=PaymentMethod.WALLET,
        mock_outcome=MockPaymentOutcome.APPROVED,
        idempotency_key="concurrent-payment-key",
    )

    def attempt() -> PaymentCommandResult:
        barrier.wait()
        return container.payment_service.process(payment_command)

    with ThreadPoolExecutor(max_workers=participants) as executor:
        results = list(executor.map(lambda _index: attempt(), range(participants)))

    assert len({result.payment.id for result in results}) == 1
    assert sum(not result.replayed for result in results) == 1
    assert sum(result.replayed for result in results) == participants - 1
    assert all(result.booking.status is BookingStatus.CONFIRMED for result in results)
    assert container.lock_manager.active_key_count == 0


def test_payment_and_cancellation_race_reaches_a_valid_terminal_state() -> None:
    container, booking_command = _container_with_one_room()
    booking = container.booking_service.create(booking_command)
    barrier = Barrier(2)
    payment_command = ProcessPaymentCommand(
        booking_id=booking.id,
        method=PaymentMethod.CARD,
        mock_outcome=MockPaymentOutcome.APPROVED,
        idempotency_key="payment-cancel-race",
    )

    def pay() -> PaymentCommandResult | ConflictError:
        barrier.wait()
        try:
            return container.payment_service.process(payment_command)
        except ConflictError as exc:
            return exc

    def cancel() -> Booking:
        barrier.wait()
        return container.cancellation_service.cancel(booking.id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        payment_future = executor.submit(pay)
        cancellation_future = executor.submit(cancel)
        payment_result = payment_future.result()
        cancellation_result = cancellation_future.result()

    assert isinstance(cancellation_result, Booking)
    assert cancellation_result.status is BookingStatus.CANCELLED
    if isinstance(payment_result, PaymentCommandResult):
        assert payment_result.booking.status is BookingStatus.CONFIRMED
    else:
        assert isinstance(payment_result, ConflictError)

    final_booking = container.booking_service.get(booking.id)
    assert final_booking.status is BookingStatus.CANCELLED
    replacement = container.booking_service.create(booking_command)
    assert replacement.status is BookingStatus.PENDING_PAYMENT
    assert container.lock_manager.active_key_count == 0
