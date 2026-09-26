"""Explicit dependency composition root."""

from __future__ import annotations

from dataclasses import dataclass

from hotieler.application.ports import Clock, IdGenerator
from hotieler.application.services import (
    AvailabilitySearchService,
    BookingService,
    CancellationService,
    CatalogService,
    PaymentService,
)
from hotieler.domain.policies import DefaultCancellationPolicy, StandardPricingStrategy
from hotieler.infrastructure.clock import SystemClock, UuidGenerator
from hotieler.infrastructure.locking import InMemoryKeyedLockManager
from hotieler.infrastructure.payments import build_payment_processors
from hotieler.infrastructure.repositories import (
    InMemoryBookingRepository,
    InMemoryOwnerRepository,
    InMemoryPaymentRepository,
    InMemoryPropertyRepository,
)


@dataclass(frozen=True, slots=True)
class AppContainer:
    owner_repository: InMemoryOwnerRepository
    property_repository: InMemoryPropertyRepository
    booking_repository: InMemoryBookingRepository
    payment_repository: InMemoryPaymentRepository
    lock_manager: InMemoryKeyedLockManager
    catalog_service: CatalogService
    availability_service: AvailabilitySearchService
    booking_service: BookingService
    payment_service: PaymentService
    cancellation_service: CancellationService


def build_container(
    *,
    clock: Clock | None = None,
    ids: IdGenerator | None = None,
) -> AppContainer:
    """Build an isolated application graph.

    Calling this function again produces fresh in-memory stores, which keeps
    tests independent without relying on global reset hooks.
    """

    resolved_clock = clock or SystemClock()
    resolved_ids = ids or UuidGenerator()
    owners = InMemoryOwnerRepository()
    properties = InMemoryPropertyRepository()
    bookings = InMemoryBookingRepository()
    payments = InMemoryPaymentRepository()
    locks = InMemoryKeyedLockManager()
    pricing = StandardPricingStrategy()
    cancellation_policy = DefaultCancellationPolicy()

    return AppContainer(
        owner_repository=owners,
        property_repository=properties,
        booking_repository=bookings,
        payment_repository=payments,
        lock_manager=locks,
        catalog_service=CatalogService(
            owners=owners,
            properties=properties,
            ids=resolved_ids,
            clock=resolved_clock,
        ),
        availability_service=AvailabilitySearchService(
            properties=properties,
            bookings=bookings,
            pricing=pricing,
            clock=resolved_clock,
        ),
        booking_service=BookingService(
            properties=properties,
            bookings=bookings,
            pricing=pricing,
            clock=resolved_clock,
            ids=resolved_ids,
            locks=locks,
        ),
        payment_service=PaymentService(
            bookings=bookings,
            payments=payments,
            processors=build_payment_processors(resolved_clock),
            clock=resolved_clock,
            ids=resolved_ids,
            locks=locks,
        ),
        cancellation_service=CancellationService(
            bookings=bookings,
            policy=cancellation_policy,
            clock=resolved_clock,
            locks=locks,
        ),
    )
