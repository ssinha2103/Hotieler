from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from hotieler.domain.entities import Booking
from hotieler.domain.enums import BookingStatus
from hotieler.domain.errors import DomainValidationError
from hotieler.domain.value_objects import Money, StayPeriod

NOW = datetime(2030, 1, 1, 10, tzinfo=UTC)


def _booking(
    *,
    status: BookingStatus,
    payment_id: UUID | None,
) -> Booking:
    return Booking(
        id=UUID(int=1),
        property_id=UUID(int=2),
        room_type_id=UUID(int=3),
        stay=StayPeriod(date(2030, 1, 10), date(2030, 1, 12)),
        guest_count=2,
        required_units=1,
        total_price=Money("2000.00"),
        created_at=NOW,
        updated_at=NOW,
        status=status,
        payment_id=payment_id,
    )


@pytest.mark.parametrize(
    "status",
    [BookingStatus.CONFIRMED, BookingStatus.PAYMENT_FAILED],
)
def test_processed_booking_states_require_payment_identifier(status: BookingStatus) -> None:
    with pytest.raises(
        DomainValidationError,
        match="processed booking requires a payment identifier",
    ):
        _booking(status=status, payment_id=None)


def test_pending_booking_forbids_payment_identifier() -> None:
    with pytest.raises(
        DomainValidationError,
        match="pending booking cannot have a payment identifier",
    ):
        _booking(status=BookingStatus.PENDING_PAYMENT, payment_id=UUID(int=99))


@pytest.mark.parametrize(
    "status",
    [BookingStatus.CONFIRMED, BookingStatus.PAYMENT_FAILED],
)
def test_processed_booking_states_accept_payment_identifier(status: BookingStatus) -> None:
    value = _booking(status=status, payment_id=UUID(int=99))

    assert value.status is status
    assert value.payment_id == UUID(int=99)
