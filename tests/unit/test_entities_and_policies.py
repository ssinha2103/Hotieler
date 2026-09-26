from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from hotieler.domain.entities import Booking, CancellationRecord, RoomType
from hotieler.domain.enums import BookingStatus, RefundStatus
from hotieler.domain.errors import (
    CancellationNotAllowedError,
    DomainValidationError,
    InvalidBookingTransitionError,
)
from hotieler.domain.policies import DefaultCancellationPolicy, StandardPricingStrategy
from hotieler.domain.value_objects import Money, StayPeriod

NOW = datetime(2030, 1, 1, 10, tzinfo=UTC)


def booking(
    *, check_in: date = date(2030, 1, 10), status: BookingStatus = BookingStatus.PENDING_PAYMENT
) -> Booking:
    created = Booking(
        id=UUID(int=1),
        property_id=UUID(int=2),
        room_type_id=UUID(int=3),
        stay=StayPeriod(check_in, date.fromordinal(check_in.toordinal() + 2)),
        guest_count=3,
        required_units=2,
        total_price=Money(Decimal("4000")),
        created_at=NOW,
        updated_at=NOW,
    )
    if status is BookingStatus.CONFIRMED:
        created.confirm(UUID(int=4), NOW)
    elif status is BookingStatus.PAYMENT_FAILED:
        created.mark_payment_failed(UUID(int=4), NOW)
    return created


def test_room_type_calculates_ceiling_room_units() -> None:
    room = RoomType(
        id=UUID(int=1),
        property_id=UUID(int=2),
        name="Deluxe",
        total_units=5,
        guests_per_unit=2,
        nightly_rate=Money(Decimal("1000")),
        amenities=frozenset({" WiFi ", "wifi", "Pool"}),
    )

    assert room.units_for(1) == 1
    assert room.units_for(2) == 1
    assert room.units_for(3) == 2
    assert room.amenities == frozenset({"wifi", "pool"})


def test_booking_owns_confirmation_and_failed_payment_transitions() -> None:
    confirmed = booking()
    failed = booking()

    confirmed.confirm(UUID(int=8), NOW)
    failed.mark_payment_failed(UUID(int=9), NOW)

    assert confirmed.status is BookingStatus.CONFIRMED
    assert confirmed.reserves_inventory
    assert failed.status is BookingStatus.PAYMENT_FAILED
    assert not failed.reserves_inventory


def test_booking_rejects_illegal_transitions() -> None:
    value = booking(status=BookingStatus.CONFIRMED)

    with pytest.raises(InvalidBookingTransitionError):
        value.confirm(UUID(int=5), NOW)
    with pytest.raises(InvalidBookingTransitionError):
        value.mark_payment_failed(UUID(int=5), NOW)


def test_booking_cancellation_is_repeat_safe_at_entity_boundary() -> None:
    value = booking(status=BookingStatus.CONFIRMED)
    cancellation = CancellationRecord(
        cancelled_at=NOW,
        refund_amount=Money(Decimal("4000")),
        refund_percentage=Decimal("100"),
        refund_status=RefundStatus.CALCULATED,
    )

    first = value.cancel(cancellation)
    second = value.cancel(cancellation)

    assert first == second
    assert value.status is BookingStatus.CANCELLED
    assert not value.reserves_inventory


def test_payment_failed_booking_cannot_be_cancelled() -> None:
    value = booking(status=BookingStatus.PAYMENT_FAILED)
    cancellation = CancellationRecord(
        cancelled_at=NOW,
        refund_amount=Money.zero(),
        refund_percentage=Decimal("0"),
        refund_status=RefundStatus.NOT_REQUIRED,
    )

    with pytest.raises(InvalidBookingTransitionError):
        value.cancel(cancellation)


def test_standard_pricing_snapshots_nights_rooms_and_rate() -> None:
    price = StandardPricingStrategy().calculate(
        StayPeriod(date(2030, 1, 10), date(2030, 1, 13)),
        2,
        Money(Decimal("1250.50")),
    )

    assert price == Money(Decimal("7503.00"))


@pytest.mark.parametrize(
    ("on_date", "expected_percent", "expected_amount", "expected_status"),
    [
        (date(2030, 1, 8), Decimal("100"), Decimal("4000.00"), RefundStatus.CALCULATED),
        (date(2030, 1, 9), Decimal("50"), Decimal("2000.00"), RefundStatus.CALCULATED),
        (date(2030, 1, 10), Decimal("0"), Decimal("0.00"), RefundStatus.NOT_REQUIRED),
    ],
)
def test_default_cancellation_policy_boundaries(
    on_date: date,
    expected_percent: Decimal,
    expected_amount: Decimal,
    expected_status: RefundStatus,
) -> None:
    quote = DefaultCancellationPolicy().quote(booking(status=BookingStatus.CONFIRMED), on_date)

    assert quote.percentage == expected_percent
    assert quote.amount.amount == expected_amount
    assert quote.status is expected_status


def test_pending_booking_cancellation_requires_no_refund() -> None:
    quote = DefaultCancellationPolicy().quote(booking(), date(2030, 1, 8))

    assert quote.amount == Money.zero()
    assert quote.status is RefundStatus.NOT_REQUIRED


def test_cancellation_after_check_in_is_rejected() -> None:
    with pytest.raises(CancellationNotAllowedError):
        DefaultCancellationPolicy().quote(
            booking(status=BookingStatus.CONFIRMED), date(2030, 1, 11)
        )


def test_entities_require_timezone_aware_timestamps() -> None:
    with pytest.raises(DomainValidationError):
        Booking(
            id=UUID(int=1),
            property_id=UUID(int=2),
            room_type_id=UUID(int=3),
            stay=StayPeriod(date(2030, 1, 10), date(2030, 1, 11)),
            guest_count=1,
            required_units=1,
            total_price=Money(Decimal("100")),
            created_at=datetime(2030, 1, 1),
            updated_at=datetime(2030, 1, 1),
        )
