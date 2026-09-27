from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from threading import Barrier
from uuid import UUID

import pytest

from hotieler.application.models import (
    CreateBookingCommand,
    CreateOwnerCommand,
    CreatePropertyCommand,
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
from hotieler.domain.entities import Booking, OwnerAccount, Property
from hotieler.domain.enums import BookingStatus, MockPaymentOutcome, PaymentMethod
from hotieler.domain.errors import (
    CancellationNotAllowedError,
    DomainValidationError,
    IdempotencyConflictError,
    InvalidBookingTransitionError,
    PropertyRoomMismatchError,
    ResourceNotFoundError,
    RoomInventoryUnavailableError,
)
from hotieler.domain.policies import DefaultCancellationPolicy, StandardPricingStrategy
from hotieler.domain.value_objects import Money, StayPeriod
from tests.unit.fakes import (
    FakeBookingRepository,
    FakeOwnerRepository,
    FakePaymentProcessor,
    FakePaymentRepository,
    FakePropertyRepository,
    FixedClock,
    IncrementingIdGenerator,
    ThreadSafeKeyedLockManager,
)

NOW = datetime(2030, 1, 1, 10, tzinfo=UTC)
STAY = StayPeriod(date(2030, 1, 10), date(2030, 1, 12))


@dataclass(slots=True)
class Services:
    owners: FakeOwnerRepository
    properties: FakePropertyRepository
    bookings: FakeBookingRepository
    payments: FakePaymentRepository
    ids: IncrementingIdGenerator
    clock: FixedClock
    locks: ThreadSafeKeyedLockManager
    catalog: CatalogService
    availability: AvailabilitySearchService
    booking: BookingService


@pytest.fixture
def services() -> Services:
    owners = FakeOwnerRepository()
    properties = FakePropertyRepository()
    bookings = FakeBookingRepository()
    payments = FakePaymentRepository()
    ids = IncrementingIdGenerator()
    clock = FixedClock(NOW)
    locks = ThreadSafeKeyedLockManager()
    pricing = StandardPricingStrategy()
    return Services(
        owners=owners,
        properties=properties,
        bookings=bookings,
        payments=payments,
        ids=ids,
        clock=clock,
        locks=locks,
        catalog=CatalogService(owners, properties, ids, clock),
        availability=AvailabilitySearchService(properties, bookings, pricing, clock),
        booking=BookingService(properties, bookings, pricing, clock, ids, locks),
    )


def onboard_property(
    services: Services,
    *,
    name: str = "Forest House",
    city: str = "Bengaluru",
    locality: str = "Indiranagar",
    star_rating: Decimal = Decimal("4.5"),
    total_units: int = 2,
    guests_per_unit: int = 2,
    nightly_rate: Decimal = Decimal("1250"),
    property_amenities: frozenset[str] = frozenset({"parking"}),
    room_amenities: frozenset[str] = frozenset({"wifi"}),
) -> tuple[OwnerAccount, Property]:
    owner = services.catalog.create_owner(
        CreateOwnerCommand(name=f"{name} Owner", contact_email="owner@example.com")
    )
    property = services.catalog.create_property(
        CreatePropertyCommand(
            owner_id=owner.id,
            name=name,
            city=city,
            locality=locality,
            address="1 Main Road",
            star_rating=star_rating,
            amenities=property_amenities,
            room_types=(
                RoomTypeInput(
                    name="Deluxe",
                    total_units=total_units,
                    guests_per_unit=guests_per_unit,
                    nightly_rate=Money(nightly_rate),
                    amenities=room_amenities,
                ),
            ),
        )
    )
    return owner, property


def create_booking(
    services: Services,
    property: Property,
    *,
    stay: StayPeriod = STAY,
    guest_count: int = 1,
) -> Booking:
    return services.booking.create(
        CreateBookingCommand(
            property_id=property.id,
            room_type_id=property.room_types[0].id,
            stay=stay,
            guest_count=guest_count,
        )
    )


def payment_service(
    services: Services,
) -> tuple[PaymentService, dict[PaymentMethod, FakePaymentProcessor]]:
    processors = {
        method: FakePaymentProcessor(NOW)
        for method in (PaymentMethod.CARD, PaymentMethod.UPI, PaymentMethod.WALLET)
    }
    return (
        PaymentService(
            services.bookings,
            services.payments,
            processors,
            services.clock,
            services.ids,
            services.locks,
        ),
        processors,
    )


def test_catalog_models_standalone_and_chain_ownership(services: Services) -> None:
    owner, first = onboard_property(services)
    second = services.catalog.create_property(
        CreatePropertyCommand(
            owner_id=owner.id,
            name="City House",
            city="Bengaluru",
            locality="MG Road",
            address="2 Main Road",
            star_rating=Decimal("4"),
            amenities=frozenset(),
            room_types=(
                RoomTypeInput(
                    name="Standard",
                    total_units=1,
                    guests_per_unit=2,
                    nightly_rate=Money(Decimal("900")),
                ),
            ),
        )
    )

    assert first.owner_id == owner.id == second.owner_id
    assert len(services.properties.list_properties()) == 2


def test_property_onboarding_requires_an_existing_owner(services: Services) -> None:
    with pytest.raises(ResourceNotFoundError):
        services.catalog.create_property(
            CreatePropertyCommand(
                owner_id=UUID(int=999),
                name="Missing Owner Hotel",
                city="Pune",
                locality="Camp",
                address="Unknown",
                star_rating=Decimal("3"),
                amenities=frozenset(),
                room_types=(
                    RoomTypeInput(
                        name="Standard",
                        total_units=1,
                        guests_per_unit=2,
                        nightly_rate=Money(Decimal("500")),
                    ),
                ),
            )
        )


def test_search_applies_all_filters_and_deterministic_order(services: Services) -> None:
    onboard_property(services, name="Zulu", nightly_rate=Decimal("1000"))
    onboard_property(services, name="Alpha", nightly_rate=Decimal("1000"))
    onboard_property(
        services,
        name="Cheaper Without Pool",
        nightly_rate=Decimal("800"),
        property_amenities=frozenset(),
    )

    results = services.availability.search(
        SearchQuery(
            city=" bengaluru ",
            locality="INDIRANAGAR",
            stay=STAY,
            guest_count=3,
            min_price=Money(Decimal("900")),
            max_price=Money(Decimal("1100")),
            amenities=frozenset({"WiFi", "Parking"}),
            min_star_rating=Decimal("4"),
        )
    )

    assert [result.property.name for result in results] == ["Alpha", "Zulu"]
    assert all(result.required_units == 2 for result in results)
    assert all(result.total_price == Money(Decimal("4000")) for result in results)


@pytest.mark.parametrize(
    "rating",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_search_query_rejects_non_finite_minimum_star_rating(rating: Decimal) -> None:
    with pytest.raises(DomainValidationError, match="star rating must be finite"):
        SearchQuery(
            city="Bengaluru",
            stay=STAY,
            guest_count=1,
            min_star_rating=rating,
        )


def test_search_excludes_only_overlapping_active_inventory(services: Services) -> None:
    _, property = onboard_property(services, total_units=1)
    first = create_booking(services, property)

    overlapping = services.availability.search(
        SearchQuery(city="Bengaluru", stay=STAY, guest_count=1)
    )
    adjacent = services.availability.search(
        SearchQuery(
            city="Bengaluru",
            stay=StayPeriod(date(2030, 1, 12), date(2030, 1, 13)),
            guest_count=1,
        )
    )

    assert overlapping == []
    assert len(adjacent) == 1
    stored = services.booking.get(first.id)
    stored.mark_payment_failed(UUID(int=900), NOW)
    services.bookings.save(stored)
    assert (
        len(services.availability.search(SearchQuery(city="Bengaluru", stay=STAY, guest_count=1)))
        == 1
    )


def test_search_uses_peak_nightly_occupancy_for_staggered_bookings(
    services: Services,
) -> None:
    _, property = onboard_property(services, total_units=2)
    create_booking(
        services,
        property,
        stay=StayPeriod(date(2030, 1, 10), date(2030, 1, 11)),
    )
    create_booking(
        services,
        property,
        stay=StayPeriod(date(2030, 1, 11), date(2030, 1, 12)),
    )

    results = services.availability.search(SearchQuery(city="Bengaluru", stay=STAY, guest_count=1))

    assert len(results) == 1
    assert results[0].available_units == 1


def test_booking_uses_peak_nightly_occupancy_and_rejects_only_true_peak_conflict(
    services: Services,
) -> None:
    _, property = onboard_property(services, total_units=2)
    create_booking(
        services,
        property,
        stay=StayPeriod(date(2030, 1, 10), date(2030, 1, 11)),
    )
    create_booking(
        services,
        property,
        stay=StayPeriod(date(2030, 1, 11), date(2030, 1, 12)),
    )

    continuous_stay = create_booking(services, property, stay=STAY)

    assert continuous_stay.required_units == 1
    with pytest.raises(RoomInventoryUnavailableError):
        create_booking(services, property, stay=STAY)


def test_peak_nightly_occupancy_preserves_multi_unit_reservations(
    services: Services,
) -> None:
    _, property = onboard_property(services, total_units=4, guests_per_unit=1)
    create_booking(
        services,
        property,
        stay=StayPeriod(date(2030, 1, 10), date(2030, 1, 11)),
        guest_count=2,
    )
    create_booking(
        services,
        property,
        stay=StayPeriod(date(2030, 1, 11), date(2030, 1, 12)),
        guest_count=2,
    )

    continuous_stay = create_booking(services, property, stay=STAY, guest_count=2)

    assert continuous_stay.required_units == 2
    with pytest.raises(RoomInventoryUnavailableError):
        create_booking(services, property, stay=STAY)


def test_booking_validates_property_room_relationship(services: Services) -> None:
    _, first = onboard_property(services, name="First")
    _, second = onboard_property(services, name="Second")

    with pytest.raises(PropertyRoomMismatchError):
        services.booking.create(
            CreateBookingCommand(
                property_id=first.id,
                room_type_id=second.room_types[0].id,
                stay=STAY,
                guest_count=1,
            )
        )


def test_booking_holds_inventory_and_snapshots_quote(services: Services) -> None:
    _, property = onboard_property(
        services,
        total_units=2,
        guests_per_unit=2,
        nightly_rate=Decimal("1000"),
    )

    created = create_booking(services, property, guest_count=3)

    assert created.required_units == 2
    assert created.total_price == Money(Decimal("4000"))
    assert created.status is BookingStatus.PENDING_PAYMENT
    with pytest.raises(RoomInventoryUnavailableError):
        create_booking(services, property)


def test_concurrent_booking_attempts_cannot_oversell(services: Services) -> None:
    _, property = onboard_property(services, total_units=1)
    attempts = 8
    barrier = Barrier(attempts)

    def attempt() -> Booking | None:
        barrier.wait()
        try:
            return create_booking(services, property)
        except RoomInventoryUnavailableError:
            return None

    with ThreadPoolExecutor(max_workers=attempts) as executor:
        outcomes = list(executor.map(lambda _: attempt(), range(attempts)))

    winners = [outcome for outcome in outcomes if outcome is not None]
    assert len(winners) == 1
    assert sum(value.required_units for value in services.bookings.list()) == 1


@pytest.mark.parametrize("method", list(PaymentMethod))
def test_payment_approval_routes_each_method_and_confirms(
    services: Services, method: PaymentMethod
) -> None:
    _, property = onboard_property(services)
    booking = create_booking(services, property)
    payments, processors = payment_service(services)

    result = payments.process(
        ProcessPaymentCommand(
            booking_id=booking.id,
            method=method,
            mock_outcome=MockPaymentOutcome.APPROVED,
            idempotency_key=f"approved-{method.value}",
        )
    )

    assert result.booking.status is BookingStatus.CONFIRMED
    assert result.booking.payment_id == result.payment.id
    assert processors[method].calls == 1


def test_payment_rejection_releases_inventory_immediately(services: Services) -> None:
    _, property = onboard_property(services, total_units=1)
    booking = create_booking(services, property)
    payments, _ = payment_service(services)

    result = payments.process(
        ProcessPaymentCommand(
            booking_id=booking.id,
            method=PaymentMethod.CARD,
            mock_outcome=MockPaymentOutcome.REJECTED,
            idempotency_key="rejected",
        )
    )

    assert result.booking.status is BookingStatus.PAYMENT_FAILED
    replacement = create_booking(services, property)
    assert replacement.status is BookingStatus.PENDING_PAYMENT


def test_payment_idempotency_replays_and_rejects_changed_fingerprint(
    services: Services,
) -> None:
    _, property = onboard_property(services)
    booking = create_booking(services, property)
    payments, processors = payment_service(services)
    command = ProcessPaymentCommand(
        booking_id=booking.id,
        method=PaymentMethod.UPI,
        mock_outcome=MockPaymentOutcome.APPROVED,
        idempotency_key="stable-key",
    )

    first = payments.process(command)
    replay = payments.process(command)

    assert not first.replayed
    assert replay.replayed
    assert replay.payment == first.payment
    assert processors[PaymentMethod.UPI].calls == 1
    with pytest.raises(IdempotencyConflictError):
        payments.process(
            ProcessPaymentCommand(
                booking_id=booking.id,
                method=PaymentMethod.UPI,
                mock_outcome=MockPaymentOutcome.REJECTED,
                idempotency_key="stable-key",
            )
        )


def test_payment_replay_returns_original_result_after_later_cancellation(
    services: Services,
) -> None:
    _, property = onboard_property(services)
    booking = create_booking(services, property)
    payments, processors = payment_service(services)
    command = ProcessPaymentCommand(
        booking_id=booking.id,
        method=PaymentMethod.CARD,
        mock_outcome=MockPaymentOutcome.APPROVED,
        idempotency_key="replay-after-cancel",
    )
    original = payments.process(command)
    cancellation = CancellationService(
        services.bookings,
        DefaultCancellationPolicy(),
        services.clock,
        services.locks,
    )
    cancellation.cancel(booking.id)

    replay = payments.process(command)

    assert original.booking.status is BookingStatus.CONFIRMED
    assert replay.booking.status is BookingStatus.CONFIRMED
    assert replay.booking.cancellation is None
    assert services.booking.get(booking.id).status is BookingStatus.CANCELLED
    assert processors[PaymentMethod.CARD].calls == 1


def test_concurrent_duplicate_payment_processes_once(services: Services) -> None:
    _, property = onboard_property(services)
    booking = create_booking(services, property)
    payments, processors = payment_service(services)
    command = ProcessPaymentCommand(
        booking_id=booking.id,
        method=PaymentMethod.WALLET,
        mock_outcome=MockPaymentOutcome.APPROVED,
        idempotency_key="concurrent-key",
    )
    barrier = Barrier(6)

    def pay() -> bool:
        barrier.wait()
        return payments.process(command).replayed

    with ThreadPoolExecutor(max_workers=6) as executor:
        replay_flags = list(executor.map(lambda _: pay(), range(6)))

    assert replay_flags.count(False) == 1
    assert replay_flags.count(True) == 5
    assert processors[PaymentMethod.WALLET].calls == 1


def test_invalid_state_is_checked_before_payment_processor(services: Services) -> None:
    _, property = onboard_property(services)
    booking = create_booking(services, property)
    cancellation = CancellationService(
        services.bookings,
        DefaultCancellationPolicy(),
        services.clock,
        services.locks,
    )
    cancellation.cancel(booking.id)
    payments, processors = payment_service(services)

    with pytest.raises(InvalidBookingTransitionError):
        payments.process(
            ProcessPaymentCommand(
                booking_id=booking.id,
                method=PaymentMethod.CARD,
                mock_outcome=MockPaymentOutcome.APPROVED,
                idempotency_key="too-late",
            )
        )

    assert processors[PaymentMethod.CARD].calls == 0


def test_confirmed_cancellation_is_repeat_safe_and_releases_inventory(
    services: Services,
) -> None:
    _, property = onboard_property(services, total_units=1)
    booking = create_booking(services, property)
    payments, _ = payment_service(services)
    payments.process(
        ProcessPaymentCommand(
            booking_id=booking.id,
            method=PaymentMethod.CARD,
            mock_outcome=MockPaymentOutcome.APPROVED,
            idempotency_key="paid",
        )
    )
    cancellation = CancellationService(
        services.bookings,
        DefaultCancellationPolicy(),
        services.clock,
        services.locks,
    )

    first = cancellation.cancel(booking.id)
    second = cancellation.cancel(booking.id)

    assert first.status is BookingStatus.CANCELLED
    assert first.cancellation == second.cancellation
    assert first.cancellation is not None
    assert first.cancellation.refund_amount == first.total_price
    assert create_booking(services, property).status is BookingStatus.PENDING_PAYMENT


def test_payment_cancellation_race_finishes_in_a_valid_terminal_state(
    services: Services,
) -> None:
    _, property = onboard_property(services)
    booking = create_booking(services, property)
    payments, _ = payment_service(services)
    cancellation = CancellationService(
        services.bookings,
        DefaultCancellationPolicy(),
        services.clock,
        services.locks,
    )
    barrier = Barrier(2)

    def pay() -> None:
        barrier.wait()
        try:
            payments.process(
                ProcessPaymentCommand(
                    booking_id=booking.id,
                    method=PaymentMethod.CARD,
                    mock_outcome=MockPaymentOutcome.REJECTED,
                    idempotency_key="race",
                )
            )
        except (CancellationNotAllowedError, InvalidBookingTransitionError):
            pass

    def cancel() -> None:
        barrier.wait()
        try:
            cancellation.cancel(booking.id)
        except InvalidBookingTransitionError:
            pass

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda action: action(), (pay, cancel)))

    final = services.booking.get(booking.id)
    assert final.status in {BookingStatus.PAYMENT_FAILED, BookingStatus.CANCELLED}
    assert not final.reserves_inventory
