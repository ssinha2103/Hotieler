"""Deterministic demonstration catalog for interactive API exploration.

The seed routine deliberately uses application services so demo records pass
through the same validation and persistence boundaries as API-created data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Lock
from uuid import UUID
from weakref import WeakKeyDictionary

from hotieler.application.models import (
    CreateOwnerCommand,
    CreatePropertyCommand,
    RoomTypeInput,
    SearchQuery,
)
from hotieler.container import AppContainer
from hotieler.domain.entities import OwnerAccount, Property
from hotieler.domain.value_objects import Money, StayPeriod
from hotieler.infrastructure.repositories import InMemoryOwnerRepository


@dataclass(frozen=True, slots=True)
class DemoSearch:
    """A ready-to-run search plus IDs useful for the next booking request."""

    label: str
    query: SearchQuery
    suggested_property_id: UUID
    suggested_room_type_id: UUID


@dataclass(frozen=True, slots=True)
class DemoDataSnapshot:
    """Immutable handles to all records created by the demo seed routine."""

    generated_for_date: date
    owner: OwnerAccount
    properties: tuple[Property, ...]
    sample_searches: tuple[DemoSearch, ...]


_snapshot_lock = Lock()
_snapshots: WeakKeyDictionary[InMemoryOwnerRepository, DemoDataSnapshot] = WeakKeyDictionary()


def seed_demo_data(
    container: AppContainer,
    *,
    as_of: date | None = None,
) -> DemoDataSnapshot:
    """Seed a realistic hotel chain once for a given in-memory container.

    ``as_of`` makes example stays deterministic in tests. At runtime, dates are
    derived from the current UTC date so the supplied searches remain valid.
    Concurrent callers for the same container receive the identical snapshot.
    """

    with _snapshot_lock:
        existing = _snapshots.get(container.owner_repository)
        if existing is not None:
            return existing

        today = as_of or datetime.now(UTC).date()
        owner = container.catalog_service.create_owner(
            CreateOwnerCommand(
                name="Hotieler Demo Hospitality",
                contact_email="demo.owner@hotieler.example",
            )
        )
        bengaluru = container.catalog_service.create_property(
            CreatePropertyCommand(
                owner_id=owner.id,
                name="Garden Courtyard Bengaluru",
                city="Bengaluru",
                locality="Indiranagar",
                address="100 Feet Road, Indiranagar, Bengaluru",
                star_rating=Decimal("4.6"),
                amenities=frozenset({"breakfast", "gym", "parking", "pool"}),
                room_types=(
                    RoomTypeInput(
                        name="Deluxe King",
                        total_units=8,
                        guests_per_unit=2,
                        nightly_rate=Money(Decimal("4500.00")),
                        amenities=frozenset({"air conditioning", "wifi", "work desk"}),
                    ),
                    RoomTypeInput(
                        name="Family Suite",
                        total_units=4,
                        guests_per_unit=4,
                        nightly_rate=Money(Decimal("7800.00")),
                        amenities=frozenset({"balcony", "kitchen", "wifi"}),
                    ),
                ),
            )
        )
        goa = container.catalog_service.create_property(
            CreatePropertyCommand(
                owner_id=owner.id,
                name="Bay Retreat Goa",
                city="Goa",
                locality="Calangute",
                address="Holiday Street, Calangute, Goa",
                star_rating=Decimal("4.4"),
                amenities=frozenset({"beach access", "breakfast", "pool", "spa"}),
                room_types=(
                    RoomTypeInput(
                        name="Garden Studio",
                        total_units=6,
                        guests_per_unit=2,
                        nightly_rate=Money(Decimal("5200.00")),
                        amenities=frozenset({"air conditioning", "wifi"}),
                    ),
                    RoomTypeInput(
                        name="Sea View Suite",
                        total_units=1,
                        guests_per_unit=3,
                        nightly_rate=Money(Decimal("9500.00")),
                        amenities=frozenset({"balcony", "sea view", "wifi"}),
                    ),
                ),
            )
        )

        snapshot = DemoDataSnapshot(
            generated_for_date=today,
            owner=owner,
            properties=(bengaluru, goa),
            sample_searches=(
                DemoSearch(
                    label="Bengaluru weekend for two",
                    query=SearchQuery(
                        city="Bengaluru",
                        locality="Indiranagar",
                        stay=StayPeriod(today + timedelta(days=30), today + timedelta(days=32)),
                        guest_count=2,
                        amenities=frozenset({"wifi", "parking"}),
                        min_star_rating=Decimal("4"),
                    ),
                    suggested_property_id=bengaluru.id,
                    suggested_room_type_id=bengaluru.room_types[0].id,
                ),
                DemoSearch(
                    label="Goa escape for three",
                    query=SearchQuery(
                        city="Goa",
                        locality="Calangute",
                        stay=StayPeriod(today + timedelta(days=45), today + timedelta(days=48)),
                        guest_count=3,
                        amenities=frozenset({"pool", "sea view", "wifi"}),
                        min_star_rating=Decimal("4"),
                    ),
                    suggested_property_id=goa.id,
                    suggested_room_type_id=goa.room_types[1].id,
                ),
            ),
        )
        _snapshots[container.owner_repository] = snapshot
        return snapshot
