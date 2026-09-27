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

    def add(self, owner: OwnerAccount) -> None:
        snapshot = deepcopy(owner)
        with self._lock:
            if snapshot.id in self._owners:
                raise DuplicateResourceError(
                    "An owner with this identifier already exists.",
                    details={"owner_id": str(snapshot.id)},
                )
            self._owners[snapshot.id] = snapshot

    def get(self, owner_id: UUID) -> OwnerAccount | None:
        with self._lock:
            owner = self._owners.get(owner_id)
            return deepcopy(owner) if owner is not None else None


class InMemoryPropertyRepository:
    def __init__(self) -> None:
        self._properties: dict[UUID, Property] = {}
        self._room_to_property: dict[UUID, UUID] = {}
        self._lock = RLock()

    def add(self, property_: Property) -> None:
        snapshot = deepcopy(property_)
        with self._lock:
            if snapshot.id in self._properties:
                raise DuplicateResourceError(
                    "A property with this identifier already exists.",
                    details={"property_id": str(snapshot.id)},
                )

            for room_type in snapshot.room_types:
                existing_property_id = self._room_to_property.get(room_type.id)
                if existing_property_id is not None:
                    raise DuplicateResourceError(
                        "A room type with this identifier already exists.",
                        details={"room_type_id": str(room_type.id)},
                    )

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

    def get_by_idempotency_key(self, key: str) -> PaymentRecord | None:
        with self._lock:
            payment_id = self._idempotency_index.get(key)
            if payment_id is None:
                return None
            payment = self._payments.get(payment_id)
            return deepcopy(payment) if payment is not None else None
