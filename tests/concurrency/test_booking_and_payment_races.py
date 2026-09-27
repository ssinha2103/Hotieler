from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from threading import Barrier, Event, Lock
from uuid import NAMESPACE_OID, UUID, uuid4

from hotieler.application.models import (
    CreateBookingCommand,
    CreateOwnerCommand,
    CreatePropertyCommand,
    PaymentCommandResult,
    ProcessPaymentCommand,
    RoomTypeInput,
)
from hotieler.application.ports import BookingRepository, KeyedLockManager, PaymentRepository
from hotieler.application.services import BookingService, PaymentService
from hotieler.container import AppContainer, build_container
from hotieler.domain.entities import Booking, PaymentRecord
from hotieler.domain.enums import BookingStatus, MockPaymentOutcome, PaymentMethod
from hotieler.domain.errors import (
    ConflictError,
    DuplicateResourceError,
    RoomInventoryUnavailableError,
)
from hotieler.domain.policies import StandardPricingStrategy
from hotieler.domain.value_objects import Money, StayPeriod
from hotieler.infrastructure.clock import DeterministicIdGenerator, FixedClock
from hotieler.infrastructure.locking import InMemoryKeyedLockManager
from hotieler.infrastructure.payments import DeterministicPaymentProcessor

_NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
_WAIT_TIMEOUT_SECONDS = 5.0


class _ObservedKeyedLockManager:
    """Expose when a second caller is queued behind one watched lock key."""

    def __init__(self, watched_key: str) -> None:
        self._watched_key = watched_key
        self._delegate = InMemoryKeyedLockManager()
        self._guard = Lock()
        self._watched_attempts = 0
        self.second_attempted = Event()

    @contextmanager
    def lock(self, key: str) -> Iterator[None]:
        if key == self._watched_key:
            with self._guard:
                self._watched_attempts += 1
                if self._watched_attempts == 2:
                    self.second_attempted.set()
        with self._delegate.lock(key):
            yield

    @property
    def watched_attempts(self) -> int:
        with self._guard:
            return self._watched_attempts

    @property
    def active_key_count(self) -> int:
        return self._delegate.active_key_count


class _NoOpKeyedLockManager:
    """Negative control proving what the services do without serialization."""

    @contextmanager
    def lock(self, key: str) -> Iterator[None]:
        del key
        yield


class _FirstListBlockingBookingRepository:
    """Hold the first inventory snapshot open inside BookingService.create."""

    def __init__(self, delegate: BookingRepository) -> None:
        self._delegate = delegate
        self._guard = Lock()
        self._list_calls = 0
        self.first_list_entered = Event()
        self.release_first_list = Event()

    def save(self, booking: Booking) -> None:
        self._delegate.save(booking)

    def get(self, booking_id: UUID) -> Booking | None:
        return self._delegate.get(booking_id)

    def list(self) -> list[Booking]:
        snapshot = self._delegate.list()
        with self._guard:
            self._list_calls += 1
            is_first = self._list_calls == 1
        if is_first:
            self.first_list_entered.set()
            if not self.release_first_list.wait(_WAIT_TIMEOUT_SECONDS):
                raise AssertionError("Timed out while holding the first inventory snapshot.")
        return snapshot

    @property
    def list_calls(self) -> int:
        with self._guard:
            return self._list_calls


class _BarrierListBookingRepository:
    """Return the same pre-write inventory snapshot to two unlocked callers."""

    def __init__(self, delegate: BookingRepository) -> None:
        self._delegate = delegate
        self._list_barrier = Barrier(2)

    def save(self, booking: Booking) -> None:
        self._delegate.save(booking)

    def get(self, booking_id: UUID) -> Booking | None:
        return self._delegate.get(booking_id)

    def list(self) -> list[Booking]:
        snapshot = self._delegate.list()
        self._list_barrier.wait(_WAIT_TIMEOUT_SECONDS)
        return snapshot


class _FirstLookupBlockingPaymentRepository:
    """Hold the first idempotency lookup open inside PaymentService.process."""

    def __init__(self, delegate: PaymentRepository) -> None:
        self._delegate = delegate
        self._guard = Lock()
        self._lookup_calls = 0
        self.first_lookup_entered = Event()
        self.release_first_lookup = Event()

    def save(self, payment: PaymentRecord) -> None:
        self._delegate.save(payment)

    def get_by_idempotency_key(self, key: str) -> PaymentRecord | None:
        snapshot = self._delegate.get_by_idempotency_key(key)
        with self._guard:
            self._lookup_calls += 1
            is_first = self._lookup_calls == 1
        if is_first:
            self.first_lookup_entered.set()
            if not self.release_first_lookup.wait(_WAIT_TIMEOUT_SECONDS):
                raise AssertionError("Timed out while holding the first idempotency lookup.")
        return snapshot

    @property
    def lookup_calls(self) -> int:
        with self._guard:
            return self._lookup_calls


class _BarrierLookupPaymentRepository:
    """Return the same missing-key snapshot to two unlocked callers."""

    def __init__(self, delegate: PaymentRepository) -> None:
        self._delegate = delegate
        self._lookup_barrier = Barrier(2)

    def save(self, payment: PaymentRecord) -> None:
        self._delegate.save(payment)

    def get_by_idempotency_key(self, key: str) -> PaymentRecord | None:
        snapshot = self._delegate.get_by_idempotency_key(key)
        self._lookup_barrier.wait(_WAIT_TIMEOUT_SECONDS)
        return snapshot


class _BarrierGetBookingRepository:
    """Give two unlocked payment calls the same pending-booking snapshot."""

    def __init__(self, delegate: BookingRepository) -> None:
        self._delegate = delegate
        self._get_barrier = Barrier(2)

    def save(self, booking: Booking) -> None:
        self._delegate.save(booking)

    def get(self, booking_id: UUID) -> Booking | None:
        snapshot = self._delegate.get(booking_id)
        self._get_barrier.wait(_WAIT_TIMEOUT_SECONDS)
        return snapshot

    def list(self) -> list[Booking]:
        return self._delegate.list()


def _container_with_one_room() -> tuple[AppContainer, CreateBookingCommand]:
    container = build_container(
        clock=FixedClock(_NOW),
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


def _booking_service(
    container: AppContainer,
    bookings: BookingRepository,
    locks: KeyedLockManager,
) -> BookingService:
    return BookingService(
        properties=container.property_repository,
        bookings=bookings,
        pricing=StandardPricingStrategy(),
        clock=FixedClock(_NOW),
        ids=DeterministicIdGenerator(NAMESPACE_OID, prefix=f"booking-race:{uuid4()}"),
        locks=locks,
    )


def _payment_service(
    container: AppContainer,
    bookings: BookingRepository,
    payments: PaymentRepository,
    locks: KeyedLockManager,
) -> tuple[PaymentService, DeterministicPaymentProcessor]:
    clock = FixedClock(_NOW)
    processor = DeterministicPaymentProcessor(PaymentMethod.WALLET, clock)
    return (
        PaymentService(
            bookings=bookings,
            payments=payments,
            processors={PaymentMethod.WALLET: processor},
            clock=clock,
            ids=DeterministicIdGenerator(NAMESPACE_OID, prefix=f"payment-race:{uuid4()}"),
            locks=locks,
        ),
        processor,
    )


def test_simultaneous_booking_attempts_have_exactly_one_winner() -> None:
    container, command = _container_with_one_room()
    repository = _FirstListBlockingBookingRepository(container.booking_repository)
    room_lock_key = f"room_type:{command.room_type_id}"
    locks = _ObservedKeyedLockManager(room_lock_key)
    service = _booking_service(container, repository, locks)

    def attempt() -> Booking | RoomInventoryUnavailableError:
        try:
            return service.create(command)
        except RoomInventoryUnavailableError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(attempt)
        assert repository.first_list_entered.wait(_WAIT_TIMEOUT_SECONDS)
        second = executor.submit(attempt)
        try:
            assert locks.second_attempted.wait(_WAIT_TIMEOUT_SECONDS)
            assert locks.watched_attempts == 2
            assert repository.list_calls == 1
        finally:
            repository.release_first_list.set()
        results = [
            first.result(timeout=_WAIT_TIMEOUT_SECONDS),
            second.result(timeout=_WAIT_TIMEOUT_SECONDS),
        ]

    winners = [result for result in results if isinstance(result, Booking)]
    conflicts = [result for result in results if isinstance(result, RoomInventoryUnavailableError)]
    assert len(winners) == 1
    assert len(conflicts) == 1
    assert repository.list_calls == 2
    assert (
        sum(
            booking.required_units
            for booking in container.booking_repository.list()
            if booking.reserves_inventory
        )
        == 1
    )
    assert locks.active_key_count == 0


def test_booking_race_control_oversells_without_the_room_type_lock() -> None:
    container, command = _container_with_one_room()
    repository = _BarrierListBookingRepository(container.booking_repository)
    service = _booking_service(container, repository, _NoOpKeyedLockManager())

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: service.create(command), range(2)))

    assert len(results) == 2
    assert len(container.booking_repository.list()) == 2
    assert sum(booking.required_units for booking in results) == 2


def test_concurrent_same_key_payment_is_processed_once_and_replayed() -> None:
    container, booking_command = _container_with_one_room()
    booking = container.booking_service.create(booking_command)
    payment_command = ProcessPaymentCommand(
        booking_id=booking.id,
        method=PaymentMethod.WALLET,
        mock_outcome=MockPaymentOutcome.APPROVED,
        idempotency_key="concurrent-payment-key",
    )
    repository = _FirstLookupBlockingPaymentRepository(container.payment_repository)
    idempotency_lock_key = f"idempotency:{payment_command.idempotency_key}"
    locks = _ObservedKeyedLockManager(idempotency_lock_key)
    service, processor = _payment_service(
        container,
        container.booking_repository,
        repository,
        locks,
    )

    def attempt() -> PaymentCommandResult:
        return service.process(payment_command)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(attempt)
        assert repository.first_lookup_entered.wait(_WAIT_TIMEOUT_SECONDS)
        second = executor.submit(attempt)
        try:
            assert locks.second_attempted.wait(_WAIT_TIMEOUT_SECONDS)
            assert locks.watched_attempts == 2
            assert repository.lookup_calls == 1
            assert processor.process_count == 0
        finally:
            repository.release_first_lookup.set()
        results = [
            first.result(timeout=_WAIT_TIMEOUT_SECONDS),
            second.result(timeout=_WAIT_TIMEOUT_SECONDS),
        ]

    assert len({result.payment.id for result in results}) == 1
    assert sum(not result.replayed for result in results) == 1
    assert sum(result.replayed for result in results) == 1
    assert all(result.booking.status is BookingStatus.CONFIRMED for result in results)
    assert repository.lookup_calls == 2
    assert processor.process_count == 1
    assert locks.active_key_count == 0


def test_payment_race_control_invokes_processor_twice_without_the_idempotency_lock() -> None:
    container, booking_command = _container_with_one_room()
    booking = container.booking_service.create(booking_command)
    booking_repository = _BarrierGetBookingRepository(container.booking_repository)
    payment_repository = _BarrierLookupPaymentRepository(container.payment_repository)
    service, processor = _payment_service(
        container,
        booking_repository,
        payment_repository,
        _NoOpKeyedLockManager(),
    )
    command = ProcessPaymentCommand(
        booking_id=booking.id,
        method=PaymentMethod.WALLET,
        mock_outcome=MockPaymentOutcome.APPROVED,
        idempotency_key="unlocked-concurrent-payment-key",
    )

    def attempt() -> PaymentCommandResult | DuplicateResourceError:
        try:
            return service.process(command)
        except DuplicateResourceError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: attempt(), range(2)))

    processed = [result for result in results if isinstance(result, PaymentCommandResult)]
    duplicates = [result for result in results if isinstance(result, DuplicateResourceError)]
    assert len(processed) == 1
    assert len(duplicates) == 1
    assert processor.process_count == 2
    assert container.payment_repository.get_by_idempotency_key(command.idempotency_key) is not None


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
