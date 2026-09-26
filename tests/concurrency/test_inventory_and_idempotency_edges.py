from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from threading import Barrier
from uuid import NAMESPACE_URL

from hotieler.application.models import (
    CreateBookingCommand,
    CreateOwnerCommand,
    CreatePropertyCommand,
    PaymentCommandResult,
    ProcessPaymentCommand,
    RoomTypeInput,
    SearchQuery,
)
from hotieler.container import AppContainer, build_container
from hotieler.domain.entities import Booking
from hotieler.domain.enums import BookingStatus, MockPaymentOutcome, PaymentMethod
from hotieler.domain.errors import (
    IdempotencyConflictError,
    InvalidBookingTransitionError,
    RoomInventoryUnavailableError,
)
from hotieler.domain.value_objects import Money, StayPeriod
from hotieler.infrastructure.clock import DeterministicIdGenerator, FixedClock

NOW = datetime(2030, 1, 1, 10, tzinfo=UTC)
STAY = StayPeriod(date(2030, 1, 10), date(2030, 1, 12))


def _container_with_inventory(
    *,
    total_units: int = 3,
    guests_per_unit: int = 2,
) -> tuple[AppContainer, CreateBookingCommand]:
    container = build_container(
        clock=FixedClock(NOW),
        ids=DeterministicIdGenerator(NAMESPACE_URL, prefix="inventory-edges"),
    )
    owner = container.catalog_service.create_owner(
        CreateOwnerCommand(name="Capacity Hotels", contact_email="capacity@example.com")
    )
    property_ = container.catalog_service.create_property(
        CreatePropertyCommand(
            owner_id=owner.id,
            name="Capacity House",
            city="Bengaluru",
            locality="Indiranagar",
            address="1 Capacity Road",
            star_rating=Decimal("4.5"),
            amenities=frozenset({"parking"}),
            room_types=(
                RoomTypeInput(
                    name="Standard",
                    total_units=total_units,
                    guests_per_unit=guests_per_unit,
                    nightly_rate=Money("1000.00"),
                    amenities=frozenset({"wifi"}),
                ),
            ),
        )
    )
    room_type = property_.room_types[0]
    return container, CreateBookingCommand(
        property_id=property_.id,
        room_type_id=room_type.id,
        stay=STAY,
        guest_count=1,
    )


def _search(container: AppContainer, guest_count: int) -> list:
    return container.availability_service.search(
        SearchQuery(city="Bengaluru", stay=STAY, guest_count=guest_count)
    )


def test_multi_unit_inventory_is_cumulative_and_released_by_cancellation() -> None:
    container, base_command = _container_with_inventory(total_units=3, guests_per_unit=2)
    two_unit_booking = container.booking_service.create(
        CreateBookingCommand(
            property_id=base_command.property_id,
            room_type_id=base_command.room_type_id,
            stay=STAY,
            guest_count=3,
        )
    )

    after_two_units = _search(container, guest_count=1)
    assert len(after_two_units) == 1
    assert after_two_units[0].required_units == 1
    assert after_two_units[0].available_units == 1
    one_unit_booking = container.booking_service.create(base_command)
    assert one_unit_booking.required_units == 1
    assert _search(container, guest_count=1) == []

    container.cancellation_service.cancel(two_unit_booking.id)

    released = _search(container, guest_count=3)
    assert len(released) == 1
    assert released[0].required_units == 2
    assert released[0].available_units == 2


def test_equal_search_quotes_have_a_stable_room_identifier_tiebreaker() -> None:
    container = build_container(
        clock=FixedClock(NOW),
        ids=DeterministicIdGenerator(NAMESPACE_URL, prefix="ordering"),
    )
    owner = container.catalog_service.create_owner(
        CreateOwnerCommand(name="Ordered Hotels", contact_email="order@example.com")
    )
    container.catalog_service.create_property(
        CreatePropertyCommand(
            owner_id=owner.id,
            name="Same Name",
            city="Bengaluru",
            locality="Indiranagar",
            address="1 Order Road",
            star_rating=Decimal("4"),
            amenities=frozenset(),
            room_types=(
                RoomTypeInput(
                    name="Same Room",
                    total_units=1,
                    guests_per_unit=2,
                    nightly_rate=Money("1000.00"),
                ),
                RoomTypeInput(
                    name="Same Room",
                    total_units=1,
                    guests_per_unit=2,
                    nightly_rate=Money("1000.00"),
                ),
            ),
        )
    )

    first = _search(container, guest_count=1)
    second = _search(container, guest_count=1)
    first_ids = [str(quote.room_type.id) for quote in first]

    assert first_ids == sorted(first_ids)
    assert [quote.room_type.id for quote in second] == [quote.room_type.id for quote in first]


def test_concurrent_two_unit_requests_never_exceed_three_unit_inventory() -> None:
    container, base_command = _container_with_inventory(total_units=3, guests_per_unit=2)
    command = CreateBookingCommand(
        property_id=base_command.property_id,
        room_type_id=base_command.room_type_id,
        stay=STAY,
        guest_count=3,
    )
    participants = 8
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
    assert len(winners) == 1
    assert winners[0].required_units == 2
    assert (
        sum(
            booking.required_units
            for booking in container.booking_repository.list()
            if booking.reserves_inventory
        )
        == 2
    )
    remaining = _search(container, guest_count=1)
    assert len(remaining) == 1
    assert remaining[0].available_units == 1
    assert container.lock_manager.active_key_count == 0


def test_concurrent_same_key_for_different_bookings_has_one_owner() -> None:
    container, command = _container_with_inventory(total_units=2)
    bookings = [container.booking_service.create(command) for _index in range(2)]
    barrier = Barrier(2)

    def pay(booking: Booking) -> PaymentCommandResult | IdempotencyConflictError:
        barrier.wait()
        try:
            return container.payment_service.process(
                ProcessPaymentCommand(
                    booking_id=booking.id,
                    method=PaymentMethod.UPI,
                    mock_outcome=MockPaymentOutcome.APPROVED,
                    idempotency_key="shared-across-bookings",
                )
            )
        except IdempotencyConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(pay, bookings))

    successes = [result for result in results if isinstance(result, PaymentCommandResult)]
    conflicts = [result for result in results if isinstance(result, IdempotencyConflictError)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    statuses = {container.booking_service.get(booking.id).status for booking in bookings}
    assert statuses == {BookingStatus.CONFIRMED, BookingStatus.PENDING_PAYMENT}
    assert container.lock_manager.active_key_count == 0


def test_concurrent_distinct_payment_keys_cannot_process_one_booking_twice() -> None:
    container, command = _container_with_inventory(total_units=1)
    booking = container.booking_service.create(command)
    barrier = Barrier(2)

    def pay(key: str) -> PaymentCommandResult | InvalidBookingTransitionError:
        barrier.wait()
        try:
            return container.payment_service.process(
                ProcessPaymentCommand(
                    booking_id=booking.id,
                    method=PaymentMethod.CARD,
                    mock_outcome=MockPaymentOutcome.APPROVED,
                    idempotency_key=key,
                )
            )
        except InvalidBookingTransitionError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(pay, ("first-key", "second-key")))

    successes = [result for result in results if isinstance(result, PaymentCommandResult)]
    conflicts = [result for result in results if isinstance(result, InvalidBookingTransitionError)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert container.booking_service.get(booking.id).status is BookingStatus.CONFIRMED
    persisted_payments = [
        container.payment_repository.get_by_idempotency_key(key)
        for key in ("first-key", "second-key")
    ]
    assert sum(payment is not None for payment in persisted_payments) == 1
    assert container.lock_manager.active_key_count == 0
