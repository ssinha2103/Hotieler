"""Thread-safe, copy-safe in-memory repository adapters."""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
from uuid import UUID

from hotieler.domain.entities import Booking, OwnerAccount, PaymentRecord, Property, RoomType
from hotieler.domain.errors import DuplicateResourceError


class InMemoryOwnerRepository:
    def __init__(self) -> None:
        self._owners: dict[UUID, OwnerAccount] = {}
        self._lock = RLock()

    def save(self, owner: OwnerAccount) -> None:
        with self._lock:
            self._owners[owner.id] = deepcopy(owner)

    def get(self, owner_id: UUID) -> OwnerAccount | None:
        with self._lock:
            owner = self._owners.get(owner_id)
            return deepcopy(owner) if owner is not None else None


class InMemoryPropertyRepository:
    def __init__(self) -> None:
        self._properties: dict[UUID, Property] = {}
        self._room_to_property: dict[UUID, UUID] = {}
        self._lock = RLock()

    def save(self, property_: Property) -> None:
        snapshot = deepcopy(property_)
        with self._lock:
            previous = self._properties.get(property_.id)
            for room_type in snapshot.room_types:
                existing_property_id = self._room_to_property.get(room_type.id)
                if existing_property_id is not None and existing_property_id != snapshot.id:
                    raise DuplicateResourceError(
                        "A room type with this identifier already exists.",
                        details={"room_type_id": str(room_type.id)},
                    )

            if previous is not None:
                for room_type in previous.room_types:
                    self._room_to_property.pop(room_type.id, None)

            self._properties[snapshot.id] = snapshot
            for room_type in snapshot.room_types:
                self._room_to_property[room_type.id] = snapshot.id

    def get(self, property_id: UUID) -> Property | None:
        with self._lock:
            property_ = self._properties.get(property_id)
            return deepcopy(property_) if property_ is not None else None

    def list_properties(self) -> list[Property]:
        with self._lock:
            return deepcopy(list(self._properties.values()))

    def get_room_type(self, room_type_id: UUID) -> RoomType | None:
        with self._lock:
            property_id = self._room_to_property.get(room_type_id)
            if property_id is None:
                return None
            property_ = self._properties.get(property_id)
            if property_ is None:
                return None
            room_type = property_.find_room_type(room_type_id)
            return deepcopy(room_type) if room_type is not None else None


class InMemoryBookingRepository:
    def __init__(self) -> None:
        self._bookings: dict[UUID, Booking] = {}
        self._lock = RLock()

    def save(self, booking: Booking) -> None:
        with self._lock:
            self._bookings[booking.id] = deepcopy(booking)

    def get(self, booking_id: UUID) -> Booking | None:
        with self._lock:
            booking = self._bookings.get(booking_id)
            return deepcopy(booking) if booking is not None else None

    def list(self) -> list[Booking]:
        with self._lock:
            return deepcopy(list(self._bookings.values()))


class InMemoryPaymentRepository:
    def __init__(self) -> None:
        self._payments: dict[UUID, PaymentRecord] = {}
        self._idempotency_index: dict[str, UUID] = {}
        self._lock = RLock()

    def save(self, payment: PaymentRecord) -> None:
        snapshot = deepcopy(payment)
        with self._lock:
            existing_id = self._idempotency_index.get(snapshot.idempotency_key)
            if existing_id is not None and existing_id != snapshot.id:
                raise DuplicateResourceError(
                    "A payment already exists for this idempotency key.",
                    details={"idempotency_key": snapshot.idempotency_key},
                )
            self._payments[snapshot.id] = snapshot
            self._idempotency_index[snapshot.idempotency_key] = snapshot.id

    def get_by_idempotency_key(self, key: str) -> PaymentRecord | None:
        with self._lock:
            payment_id = self._idempotency_index.get(key)
            if payment_id is None:
                return None
            payment = self._payments.get(payment_id)
            return deepcopy(payment) if payment is not None else None
