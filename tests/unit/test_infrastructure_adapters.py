from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from hotieler.domain.entities import Booking, PaymentRecord
from hotieler.domain.enums import (
    BookingStatus,
    MockPaymentOutcome,
    PaymentMethod,
    PaymentStatus,
)
from hotieler.domain.errors import DuplicateResourceError
from hotieler.domain.value_objects import Money, StayPeriod
from hotieler.infrastructure.locking import InMemoryKeyedLockManager
from hotieler.infrastructure.repositories import (
    InMemoryBookingRepository,
    InMemoryPaymentRepository,
)

NOW = datetime(2030, 1, 1, 10, tzinfo=UTC)


def _booking(identifier: int = 1) -> Booking:
    return Booking(
        id=UUID(int=identifier),
        property_id=UUID(int=100),
        room_type_id=UUID(int=200),
        stay=StayPeriod(date(2030, 1, 10), date(2030, 1, 12)),
        guest_count=2,
        required_units=1,
        total_price=Money("2000.00"),
        created_at=NOW,
        updated_at=NOW,
    )


def _payment(*, identifier: int, key: str) -> PaymentRecord:
    return PaymentRecord(
        id=UUID(int=identifier),
        booking_id=UUID(int=1),
        method=PaymentMethod.CARD,
        amount=Money("2000.00"),
        status=PaymentStatus.APPROVED,
        mock_outcome=MockPaymentOutcome.APPROVED,
        provider_reference=f"provider-{identifier}",
        idempotency_key=key,
        fingerprint=f"fingerprint-{identifier}",
        booking_status_after=BookingStatus.CONFIRMED,
        created_at=NOW,
    )


def test_booking_repository_copies_on_write_get_and_list() -> None:
    repository = InMemoryBookingRepository()
    source = _booking()
    repository.save(source)

    source.confirm(UUID(int=301), NOW)
    stored_after_source_mutation = repository.get(source.id)
    assert stored_after_source_mutation is not None
    assert stored_after_source_mutation.status is BookingStatus.PENDING_PAYMENT

    fetched = repository.get(source.id)
    assert fetched is not None
    fetched.mark_payment_failed(UUID(int=302), NOW)
    stored_after_fetched_mutation = repository.get(source.id)
    assert stored_after_fetched_mutation is not None
    assert stored_after_fetched_mutation.status is BookingStatus.PENDING_PAYMENT

    listed = repository.list()
    listed[0].confirm(UUID(int=303), NOW)
    persisted = repository.get(source.id)
    assert persisted is not None
    assert persisted.status is BookingStatus.PENDING_PAYMENT
    assert persisted.payment_id is None


def test_payment_repository_rejects_duplicate_key_without_replacing_original() -> None:
    repository = InMemoryPaymentRepository()
    original = _payment(identifier=41, key="same-key")
    duplicate = _payment(identifier=42, key="same-key")
    repository.save(original)

    with pytest.raises(DuplicateResourceError):
        repository.save(duplicate)

    stored = repository.get_by_idempotency_key("same-key")
    assert stored == original
    assert stored is not original


def test_keyed_lock_manager_reuses_reentrant_key_and_reclaims_all_entries() -> None:
    manager = InMemoryKeyedLockManager()

    with manager.lock("booking:1"):
        assert manager.active_key_count == 1
        with manager.lock("booking:1"):
            assert manager.active_key_count == 1
        assert manager.active_key_count == 1
        with manager.lock("booking:2"):
            assert manager.active_key_count == 2
        assert manager.active_key_count == 1

    assert manager.active_key_count == 0


def test_keyed_lock_manager_reclaims_entry_after_exception() -> None:
    manager = InMemoryKeyedLockManager()

    with pytest.raises(RuntimeError, match="forced failure"):
        with manager.lock("booking:1"):
            assert manager.active_key_count == 1
            raise RuntimeError("forced failure")

    assert manager.active_key_count == 0
    with pytest.raises(ValueError, match="must not be blank"):
        with manager.lock("  "):
            raise AssertionError("blank keys must not enter the context")
    assert manager.active_key_count == 0
