"""Composable predicates for availability-search catalog filters."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, TypeVar

from hotieler.domain.entities import Property, RoomType, normalize_amenities
from hotieler.domain.value_objects import Money

T_contra = TypeVar("T_contra", contravariant=True)


class Specification(Protocol[T_contra]):
    def is_satisfied_by(self, candidate: T_contra) -> bool:
        """Return whether the candidate meets this specification."""


@dataclass(frozen=True, slots=True)
class AndSpecification[T]:
    specifications: tuple[Specification[T], ...]

    def is_satisfied_by(self, candidate: T) -> bool:
        return all(
            specification.is_satisfied_by(candidate) for specification in self.specifications
        )


@dataclass(frozen=True, slots=True)
class PropertyRoomCandidate:
    property: Property
    room_type: RoomType


@dataclass(frozen=True, slots=True)
class CitySpecification:
    city: str

    def is_satisfied_by(self, candidate: PropertyRoomCandidate) -> bool:
        return candidate.property.city.strip().casefold() == self.city.strip().casefold()


@dataclass(frozen=True, slots=True)
class LocalitySpecification:
    locality: str

    def is_satisfied_by(self, candidate: PropertyRoomCandidate) -> bool:
        return candidate.property.locality.strip().casefold() == self.locality.strip().casefold()


@dataclass(frozen=True, slots=True)
class AmenitiesSpecification:
    required: frozenset[str]

    def is_satisfied_by(self, candidate: PropertyRoomCandidate) -> bool:
        requested = normalize_amenities(self.required)
        provided = candidate.property.amenities | candidate.room_type.amenities
        return requested.issubset(provided)


@dataclass(frozen=True, slots=True)
class MinStarRatingSpecification:
    minimum: Decimal

    def is_satisfied_by(self, candidate: PropertyRoomCandidate) -> bool:
        return candidate.property.star_rating >= self.minimum


@dataclass(frozen=True, slots=True)
class NightlyRateSpecification:
    minimum: Money | None = None
    maximum: Money | None = None

    def is_satisfied_by(self, candidate: PropertyRoomCandidate) -> bool:
        rate = candidate.room_type.nightly_rate
        if self.minimum is not None:
            rate.require_same_currency(self.minimum)
            if rate.amount < self.minimum.amount:
                return False
        if self.maximum is not None:
            rate.require_same_currency(self.maximum)
            if rate.amount > self.maximum.amount:
                return False
        return True
