from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from uuid import NAMESPACE_URL

import pytest

from hotieler.container import build_container
from hotieler.demo_data import DemoDataSnapshot, seed_demo_data
from hotieler.infrastructure.clock import DeterministicIdGenerator, FixedClock


@pytest.fixture
def demo_snapshot() -> DemoDataSnapshot:
    container = build_container(
        clock=FixedClock(datetime(2030, 1, 1, 10, tzinfo=UTC)),
        ids=DeterministicIdGenerator(NAMESPACE_URL, prefix="demo-data"),
    )
    return seed_demo_data(container, as_of=date(2030, 1, 1))


def test_seed_creates_chain_with_distinct_properties_and_room_options(
    demo_snapshot: DemoDataSnapshot,
) -> None:
    assert demo_snapshot.owner.name == "Hotieler Demo Hospitality"
    assert len(demo_snapshot.properties) == 2
    assert {property_.city for property_ in demo_snapshot.properties} == {"Bengaluru", "Goa"}
    assert all(
        property_.owner_id == demo_snapshot.owner.id for property_ in demo_snapshot.properties
    )
    assert all(len(property_.room_types) >= 2 for property_ in demo_snapshot.properties)
    assert (
        len(
            {
                room.nightly_rate.amount
                for property_ in demo_snapshot.properties
                for room in property_.room_types
            }
        )
        == 4
    )
    assert all(property_.amenities for property_ in demo_snapshot.properties)
    assert all(
        room.amenities for property_ in demo_snapshot.properties for room in property_.room_types
    )


def test_seed_uses_services_and_persists_the_returned_entities() -> None:
    container = build_container(
        clock=FixedClock(datetime(2030, 1, 1, 10, tzinfo=UTC)),
        ids=DeterministicIdGenerator(NAMESPACE_URL, prefix="demo-persistence"),
    )

    snapshot = seed_demo_data(container, as_of=date(2030, 1, 1))

    assert container.owner_repository.get(snapshot.owner.id) == snapshot.owner
    assert container.property_repository.list_properties() == list(snapshot.properties)
    for property_ in snapshot.properties:
        assert container.property_repository.get(property_.id) == property_
        for room in property_.room_types:
            assert container.property_repository.get_room_type(room.id) == room


def test_sample_searches_are_future_dated_and_return_suggested_rooms() -> None:
    as_of = date(2030, 1, 1)
    container = build_container(
        clock=FixedClock(datetime(2030, 1, 1, 10, tzinfo=UTC)),
        ids=DeterministicIdGenerator(NAMESPACE_URL, prefix="demo-search"),
    )
    snapshot = seed_demo_data(container, as_of=as_of)

    assert len(snapshot.sample_searches) == 2
    for sample in snapshot.sample_searches:
        assert sample.query.stay.check_in > as_of
        quotes = container.availability_service.search(sample.query)
        assert any(
            quote.property.id == sample.suggested_property_id
            and quote.room_type.id == sample.suggested_room_type_id
            for quote in quotes
        )


def test_seed_is_idempotent_for_sequential_and_concurrent_calls() -> None:
    container = build_container(
        clock=FixedClock(datetime(2030, 1, 1, 10, tzinfo=UTC)),
        ids=DeterministicIdGenerator(NAMESPACE_URL, prefix="demo-idempotency"),
    )

    first = seed_demo_data(container, as_of=date(2030, 1, 1))
    second = seed_demo_data(container, as_of=date(2040, 1, 1))
    with ThreadPoolExecutor(max_workers=8) as executor:
        concurrent_results = list(
            executor.map(
                lambda _: seed_demo_data(container, as_of=date(2050, 1, 1)),
                range(16),
            )
        )

    assert second is first
    assert all(result is first for result in concurrent_results)
    assert len(container.property_repository.list_properties()) == 2
