from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID

import pytest

from hotieler.domain.entities import Property, RoomType
from hotieler.domain.errors import DomainValidationError
from hotieler.domain.policies import StandardPricingStrategy
from hotieler.domain.specifications import NightlyRateSpecification, PropertyRoomCandidate
from hotieler.domain.value_objects import Money, StayPeriod
from hotieler.infrastructure.clock import (
    DeterministicIdGenerator,
    FixedClock,
    SystemClock,
    UuidGenerator,
)
from hotieler.infrastructure.repositories import InMemoryPropertyRepository

NOW = datetime(2030, 1, 1, 10, tzinfo=UTC)


def _candidate(*, nightly_rate: str = "1500.00") -> PropertyRoomCandidate:
    property_id = UUID(int=1)
    room_type = RoomType(
        id=UUID(int=2),
        property_id=property_id,
        name="Standard",
        total_units=2,
        guests_per_unit=2,
        nightly_rate=Money(nightly_rate),
    )
    property_ = Property(
        id=property_id,
        owner_id=UUID(int=3),
        name="Example Hotel",
        city="Bengaluru",
        locality="Indiranagar",
        address="1 Main Road",
        star_rating=Decimal("4.5"),
        amenities=frozenset({"wifi"}),
        room_types=(room_type,),
        created_at=NOW,
    )
    return PropertyRoomCandidate(property=property_, room_type=room_type)


@pytest.mark.parametrize("required_units", [0, -1])
def test_standard_pricing_rejects_non_positive_room_units(required_units: int) -> None:
    with pytest.raises(
        DomainValidationError,
        match="Required room units must be greater than zero",
    ):
        StandardPricingStrategy().calculate(
            StayPeriod(date(2030, 1, 10), date(2030, 1, 11)),
            required_units,
            Money("1000.00"),
        )


def test_nightly_rate_specification_rejects_rate_above_maximum() -> None:
    specification = NightlyRateSpecification(maximum=Money("1499.99"))

    assert specification.is_satisfied_by(_candidate()) is False


def test_system_clock_returns_current_utc_timestamp() -> None:
    before = datetime.now(UTC)
    observed = SystemClock().now()
    after = datetime.now(UTC)

    assert before <= observed <= after
    assert observed.tzinfo is UTC


def test_fixed_clock_rejects_naive_initial_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2030, 1, 1, 10))


def test_fixed_clock_rejects_naive_replacement_without_mutating_time() -> None:
    clock = FixedClock(NOW)

    with pytest.raises(ValueError, match="timezone-aware"):
        clock.set(datetime(2030, 1, 2, 10))

    assert clock.now() == NOW


def test_fixed_clock_normalizes_replacement_to_utc() -> None:
    clock = FixedClock(NOW)
    offset_time = datetime(2030, 1, 2, 15, tzinfo=timezone(timedelta(hours=5)))

    clock.set(offset_time)

    assert clock.now() == datetime(2030, 1, 2, 10, tzinfo=UTC)


def test_uuid_generators_return_unique_and_repeatable_identifiers() -> None:
    random_ids = (UuidGenerator().new(), UuidGenerator().new())
    deterministic = DeterministicIdGenerator(NAMESPACE_URL, prefix="coverage")
    deterministic_ids = (deterministic.new(), deterministic.new())
    replay = DeterministicIdGenerator(NAMESPACE_URL, prefix="coverage")

    assert all(isinstance(identifier, UUID) for identifier in random_ids)
    assert random_ids[0] != random_ids[1]
    assert deterministic_ids == (replay.new(), replay.new())
    assert deterministic_ids[0] != deterministic_ids[1]


def test_property_repository_handles_orphaned_room_index_defensively() -> None:
    repository = InMemoryPropertyRepository()
    room_type_id = UUID(int=10)
    repository._room_to_property[room_type_id] = UUID(int=11)

    assert repository.get_room_type(room_type_id) is None


def test_property_repository_handles_room_index_mismatch_defensively() -> None:
    repository = InMemoryPropertyRepository()
    candidate = _candidate()
    repository.add(candidate.property)
    mismatched_room_type_id = UUID(int=12)
    repository._room_to_property[mismatched_room_type_id] = candidate.property.id

    assert repository.get_room_type(mismatched_room_type_id) is None
