from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from hotieler.application.ports import OwnerRepository, PaymentRepository, PropertyRepository
from hotieler.domain.entities import OwnerAccount, PaymentRecord, Property, RoomType
from hotieler.domain.enums import (
    BookingStatus,
    MockPaymentOutcome,
    PaymentMethod,
    PaymentStatus,
)
from hotieler.domain.errors import DuplicateResourceError
from hotieler.domain.value_objects import Money
from hotieler.infrastructure.repositories import (
    InMemoryOwnerRepository,
    InMemoryPaymentRepository,
    InMemoryPropertyRepository,
)
from tests.unit.fakes import FakeOwnerRepository, FakePaymentRepository, FakePropertyRepository

NOW = datetime(2030, 1, 1, 10, tzinfo=UTC)

OwnerRepositoryFactory = Callable[[], OwnerRepository]
PropertyRepositoryFactory = Callable[[], PropertyRepository]
PaymentRepositoryFactory = Callable[[], PaymentRepository]

OWNER_REPOSITORIES: tuple[OwnerRepositoryFactory, ...] = (
    InMemoryOwnerRepository,
    FakeOwnerRepository,
)
PROPERTY_REPOSITORIES: tuple[PropertyRepositoryFactory, ...] = (
    InMemoryPropertyRepository,
    FakePropertyRepository,
)
PAYMENT_REPOSITORIES: tuple[PaymentRepositoryFactory, ...] = (
    InMemoryPaymentRepository,
    FakePaymentRepository,
)


def _owner(identifier: int, name: str) -> OwnerAccount:
    return OwnerAccount(
        id=UUID(int=identifier),
        name=name,
        contact_email=f"owner-{identifier}@example.com",
        created_at=NOW,
    )


def _property(*, property_id: int, room_type_id: int, name: str) -> Property:
    resolved_property_id = UUID(int=property_id)
    room_type = RoomType(
        id=UUID(int=room_type_id),
        property_id=resolved_property_id,
        name="Standard",
        total_units=2,
        guests_per_unit=2,
        nightly_rate=Money("1000.00"),
    )
    return Property(
        id=resolved_property_id,
        owner_id=UUID(int=900),
        name=name,
        city="Bengaluru",
        locality="Indiranagar",
        address="1 Main Road",
        star_rating=Decimal("4.5"),
        amenities=frozenset({"wifi"}),
        room_types=(room_type,),
        created_at=NOW,
    )


def _payment(*, identifier: int, booking_id: int, key: str) -> PaymentRecord:
    return PaymentRecord(
        id=UUID(int=identifier),
        booking_id=UUID(int=booking_id),
        method=PaymentMethod.CARD,
        amount=Money("1000.00"),
        status=PaymentStatus.APPROVED,
        mock_outcome=MockPaymentOutcome.APPROVED,
        provider_reference=f"provider-{identifier}",
        idempotency_key=key,
        fingerprint=f"fingerprint-{identifier}-{booking_id}-{key}",
        booking_status_after=BookingStatus.CONFIRMED,
        created_at=NOW,
    )


@pytest.mark.parametrize(
    "repository_factory",
    OWNER_REPOSITORIES,
    ids=("in-memory", "fake"),
)
def test_owner_repository_add_rejects_duplicate_identifier_without_replacement(
    repository_factory: OwnerRepositoryFactory,
) -> None:
    repository = repository_factory()
    original = _owner(1, "Original Owner")
    duplicate = replace(original, name="Replacement Owner")
    repository.add(original)

    with pytest.raises(DuplicateResourceError) as error:
        repository.add(duplicate)

    assert error.value.message == "An owner with this identifier already exists."
    assert error.value.details == {"owner_id": str(original.id)}
    stored = repository.get(original.id)
    assert stored == original
    assert stored is not original


@pytest.mark.parametrize(
    "repository_factory",
    PROPERTY_REPOSITORIES,
    ids=("in-memory", "fake"),
)
def test_property_repository_add_rejects_duplicate_identifier_without_replacement(
    repository_factory: PropertyRepositoryFactory,
) -> None:
    repository = repository_factory()
    original = _property(property_id=10, room_type_id=11, name="Original")
    duplicate = replace(original, name="Replacement")
    repository.add(original)

    with pytest.raises(DuplicateResourceError) as error:
        repository.add(duplicate)

    assert error.value.message == "A property with this identifier already exists."
    assert error.value.details == {"property_id": str(original.id)}
    stored = repository.get(original.id)
    assert stored == original
    assert stored is not original
    assert repository.get_room_type(original.room_types[0].id) == original.room_types[0]


@pytest.mark.parametrize(
    "repository_factory",
    PROPERTY_REPOSITORIES,
    ids=("in-memory", "fake"),
)
def test_property_repository_add_rejects_cross_property_room_identifier_collision_atomically(
    repository_factory: PropertyRepositoryFactory,
) -> None:
    repository = repository_factory()
    original = _property(property_id=20, room_type_id=21, name="Original")
    conflict = _property(property_id=30, room_type_id=21, name="Conflict")
    repository.add(original)

    with pytest.raises(DuplicateResourceError) as error:
        repository.add(conflict)

    assert error.value.message == "A room type with this identifier already exists."
    assert error.value.details == {"room_type_id": str(original.room_types[0].id)}
    assert repository.get(conflict.id) is None
    assert repository.list_properties() == [original]
    assert repository.get_room_type(original.room_types[0].id) == original.room_types[0]


@pytest.mark.parametrize(
    "repository_factory",
    PAYMENT_REPOSITORIES,
    ids=("in-memory", "fake"),
)
def test_payment_repository_rejects_duplicate_identifier_atomically(
    repository_factory: PaymentRepositoryFactory,
) -> None:
    repository = repository_factory()
    original = _payment(identifier=41, booking_id=1, key="original-key")
    conflict = _payment(identifier=41, booking_id=2, key="attempted-key")
    repository.save(original)

    with pytest.raises(DuplicateResourceError) as error:
        repository.save(conflict)

    assert error.value.message == "A payment with this identifier already exists."
    assert error.value.details == {"payment_id": str(original.id)}
    stored = repository.get_by_idempotency_key("original-key")
    assert stored == original
    assert stored is not original
    assert repository.get_by_idempotency_key("attempted-key") is None

    recovered = _payment(identifier=42, booking_id=2, key="attempted-key")
    repository.save(recovered)
    assert repository.get_by_idempotency_key("attempted-key") == recovered


@pytest.mark.parametrize(
    "repository_factory",
    PAYMENT_REPOSITORIES,
    ids=("in-memory", "fake"),
)
def test_payment_repository_rejects_duplicate_idempotency_key_atomically(
    repository_factory: PaymentRepositoryFactory,
) -> None:
    repository = repository_factory()
    original = _payment(identifier=51, booking_id=1, key="shared-key")
    conflict = _payment(identifier=52, booking_id=2, key="shared-key")
    repository.save(original)

    with pytest.raises(DuplicateResourceError) as error:
        repository.save(conflict)

    assert error.value.message == "A payment already exists for this idempotency key."
    assert error.value.details == {"idempotency_key": "shared-key"}
    stored = repository.get_by_idempotency_key("shared-key")
    assert stored == original
    assert stored is not original

    recovered = _payment(identifier=52, booking_id=2, key="recovered-key")
    repository.save(recovered)
    assert repository.get_by_idempotency_key("recovered-key") == recovered


@pytest.mark.parametrize(
    "repository_factory",
    PAYMENT_REPOSITORIES,
    ids=("in-memory", "fake"),
)
def test_payment_repository_rejects_second_payment_for_booking_atomically(
    repository_factory: PaymentRepositoryFactory,
) -> None:
    repository = repository_factory()
    original = _payment(identifier=61, booking_id=1, key="original-key")
    conflict = _payment(identifier=62, booking_id=1, key="attempted-key")
    repository.save(original)

    with pytest.raises(DuplicateResourceError) as error:
        repository.save(conflict)

    assert error.value.message == "A payment already exists for this booking."
    assert error.value.details == {"booking_id": str(original.booking_id)}
    stored = repository.get_by_idempotency_key("original-key")
    assert stored == original
    assert stored is not original
    assert repository.get_by_idempotency_key("attempted-key") is None

    recovered = _payment(identifier=62, booking_id=2, key="attempted-key")
    repository.save(recovered)
    assert repository.get_by_idempotency_key("attempted-key") == recovered
