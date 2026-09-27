"""Focused boundary tests for defensive application and API branches."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, DecimalException
from uuid import UUID

import pytest

from hotieler.api.schemas import money_response
from hotieler.application.models import CreateBookingCommand, SearchQuery
from hotieler.container import build_container
from hotieler.domain.errors import DomainValidationError
from hotieler.domain.value_objects import Money, StayPeriod
from hotieler.infrastructure.clock import FixedClock


def test_search_query_rejects_a_non_numeric_runtime_star_rating() -> None:
    with pytest.raises(
        DomainValidationError,
        match="Minimum star rating must be numeric",
    ) as captured:
        SearchQuery(
            city="Bengaluru",
            stay=StayPeriod(date(2030, 1, 10), date(2030, 1, 12)),
            guest_count=1,
            min_star_rating="not-a-number",  # type: ignore[arg-type]
        )

    assert isinstance(captured.value.__cause__, DecimalException)


def test_booking_service_rejects_non_positive_guests_before_repository_lookup() -> None:
    container = build_container(clock=FixedClock(datetime(2030, 1, 1, 10, tzinfo=UTC)))

    with pytest.raises(DomainValidationError, match="Guest count must be greater than zero"):
        container.booking_service.create(
            CreateBookingCommand(
                property_id=UUID(int=1),
                room_type_id=UUID(int=2),
                stay=StayPeriod(date(2030, 1, 10), date(2030, 1, 12)),
                guest_count=0,
            )
        )

    assert container.booking_repository.list() == []


def test_money_response_rejects_a_currency_outside_the_public_inr_contract() -> None:
    with pytest.raises(ValueError, match="only support INR"):
        money_response(Money(Decimal("100.00"), "USD"))
