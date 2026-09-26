"""Narrow ports implemented by infrastructure adapters."""

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol
from uuid import UUID

from hotieler.application.models import PaymentProcessorResult
from hotieler.domain.entities import Booking, OwnerAccount, PaymentRecord, Property, RoomType
from hotieler.domain.enums import MockPaymentOutcome
from hotieler.domain.value_objects import Money


class OwnerRepository(Protocol):
    def add(self, owner: OwnerAccount) -> None: ...

    def get(self, owner_id: UUID) -> OwnerAccount | None: ...


class PropertyRepository(Protocol):
    def add(self, property: Property) -> None: ...

    def get(self, property_id: UUID) -> Property | None: ...

    def list_properties(self) -> list[Property]: ...

    def get_room_type(self, room_type_id: UUID) -> RoomType | None: ...


class BookingRepository(Protocol):
    def save(self, booking: Booking) -> None: ...

    def get(self, booking_id: UUID) -> Booking | None: ...

    def list(self) -> list[Booking]: ...


class PaymentRepository(Protocol):
    def save(self, payment: PaymentRecord) -> None: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> PaymentRecord | None: ...


class PaymentProcessor(Protocol):
    def process(
        self,
        booking_id: UUID,
        amount: Money,
        mock_outcome: MockPaymentOutcome,
    ) -> PaymentProcessorResult: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new(self) -> UUID: ...


class KeyedLockManager(Protocol):
    def lock(self, key: str) -> AbstractContextManager[None]: ...
