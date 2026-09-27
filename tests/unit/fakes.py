"""Small contract-respecting test doubles for application-service tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from threading import Lock, RLock
from uuid import UUID

from hotieler.application.models import PaymentProcessorResult
from hotieler.domain.entities import Booking, OwnerAccount, PaymentRecord, Property, RoomType
from hotieler.domain.enums import MockPaymentOutcome, PaymentStatus
from hotieler.domain.errors import DuplicateResourceError
from hotieler.domain.value_objects import Money


class FakeOwnerRepository:
    def __init__(self) -> None:
        self._values: dict[UUID, OwnerAccount] = {}
        self._lock = RLock()

    def add(self, owner: OwnerAccount) -> None:
        snapshot = deepcopy(owner)
        with self._lock:
            if snapshot.id in self._values:
                raise DuplicateResourceError(
                    "An owner with this identifier already exists.",
                    details={"owner_id": str(snapshot.id)},
                )
            self._values[snapshot.id] = snapshot

    def get(self, owner_id: UUID) -> OwnerAccount | None:
        with self._lock:
            value = self._values.get(owner_id)
            return deepcopy(value) if value is not None else None


class FakePropertyRepository:
    def __init__(self) -> None:
        self._values: dict[UUID, Property] = {}
        self._room_to_property: dict[UUID, UUID] = {}
        self._lock = RLock()

    def add(self, property: Property) -> None:
        snapshot = deepcopy(property)
        with self._lock:
            if snapshot.id in self._values:
                raise DuplicateResourceError(
                    "A property with this identifier already exists.",
                    details={"property_id": str(snapshot.id)},
                )
            for room_type in snapshot.room_types:
                if room_type.id in self._room_to_property:
                    raise DuplicateResourceError(
                        "A room type with this identifier already exists.",
                        details={"room_type_id": str(room_type.id)},
                    )
            self._values[snapshot.id] = snapshot
            for room_type in snapshot.room_types:
                self._room_to_property[room_type.id] = snapshot.id

    def get(self, property_id: UUID) -> Property | None:
        with self._lock:
            value = self._values.get(property_id)
            return deepcopy(value) if value is not None else None

    def list_properties(self) -> list[Property]:
        with self._lock:
            return deepcopy(list(self._values.values()))

    def get_room_type(self, room_type_id: UUID) -> RoomType | None:
        with self._lock:
            property_id = self._room_to_property.get(room_type_id)
            if property_id is None:
                return None
            property = self._values.get(property_id)
            if property is None:
                return None
            room = property.find_room_type(room_type_id)
            return deepcopy(room) if room is not None else None


class FakeBookingRepository:
    def __init__(self) -> None:
        self._values: dict[UUID, Booking] = {}
        self._lock = RLock()

    def save(self, booking: Booking) -> None:
        with self._lock:
            self._values[booking.id] = deepcopy(booking)

    def get(self, booking_id: UUID) -> Booking | None:
        with self._lock:
            value = self._values.get(booking_id)
            return deepcopy(value) if value is not None else None

    def list(self) -> list[Booking]:
        with self._lock:
            return deepcopy(list(self._values.values()))


class FakePaymentRepository:
    def __init__(self) -> None:
        self._payments: dict[UUID, PaymentRecord] = {}
        self._idempotency_index: dict[str, UUID] = {}
        self._booking_index: dict[UUID, UUID] = {}
        self._lock = RLock()

    def save(self, payment: PaymentRecord) -> None:
        snapshot = deepcopy(payment)
        with self._lock:
            if snapshot.id in self._payments:
                raise DuplicateResourceError(
                    "A payment with this identifier already exists.",
                    details={"payment_id": str(snapshot.id)},
                )
            existing_id = self._idempotency_index.get(snapshot.idempotency_key)
            if existing_id is not None and existing_id != snapshot.id:
                raise DuplicateResourceError(
                    "A payment already exists for this idempotency key.",
                    details={"idempotency_key": snapshot.idempotency_key},
                )
            existing_id = self._booking_index.get(snapshot.booking_id)
            if existing_id is not None and existing_id != snapshot.id:
                raise DuplicateResourceError(
                    "A payment already exists for this booking.",
                    details={"booking_id": str(snapshot.booking_id)},
                )
            self._payments[snapshot.id] = snapshot
            self._idempotency_index[snapshot.idempotency_key] = snapshot.id
            self._booking_index[snapshot.booking_id] = snapshot.id

    def get_by_idempotency_key(self, idempotency_key: str) -> PaymentRecord | None:
        with self._lock:
            payment_id = self._idempotency_index.get(idempotency_key)
            if payment_id is None:
                return None
            value = self._payments.get(payment_id)
            return deepcopy(value) if value is not None else None


class IncrementingIdGenerator:
    def __init__(self, start: int = 1) -> None:
        self._next = start
        self._lock = Lock()

    def new(self) -> UUID:
        with self._lock:
            value = UUID(int=self._next)
            self._next += 1
            return value


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class ThreadSafeKeyedLockManager:
    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[str, RLock] = {}

    @contextmanager
    def lock(self, key: str) -> Iterator[None]:
        with self._guard:
            lock = self._locks.setdefault(key, RLock())
        with lock:
            yield


class FakePaymentProcessor:
    def __init__(self, processed_at: datetime) -> None:
        self._processed_at = processed_at
        self._guard = Lock()
        self.calls = 0

    def process(
        self,
        booking_id: UUID,
        amount: Money,
        mock_outcome: MockPaymentOutcome,
    ) -> PaymentProcessorResult:
        del amount
        with self._guard:
            self.calls += 1
            call_number = self.calls
        return PaymentProcessorResult(
            status=(
                PaymentStatus.APPROVED
                if mock_outcome is MockPaymentOutcome.APPROVED
                else PaymentStatus.REJECTED
            ),
            provider_reference=f"mock-{booking_id}-{call_number}",
            processed_at=self._processed_at,
        )
