"""Replaceable pricing and cancellation policies."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from hotieler.domain.entities import Booking
from hotieler.domain.enums import BookingStatus, RefundStatus
from hotieler.domain.errors import CancellationNotAllowedError, DomainValidationError
from hotieler.domain.value_objects import Money, StayPeriod


class PricingStrategy(Protocol):
    def calculate(self, stay: StayPeriod, required_units: int, nightly_rate: Money) -> Money:
        """Calculate a complete stay quote."""


class StandardPricingStrategy:
    """Nightly base rate multiplied by nights and required room units."""

    def calculate(self, stay: StayPeriod, required_units: int, nightly_rate: Money) -> Money:
        if required_units <= 0:
            raise DomainValidationError("Required room units must be greater than zero.")
        return nightly_rate.multiply(stay.nights * required_units)


@dataclass(frozen=True, slots=True)
class RefundQuote:
    amount: Money
    percentage: Decimal
    status: RefundStatus


class CancellationPolicy(Protocol):
    def quote(self, booking: Booking, on_date: date) -> RefundQuote:
        """Calculate the refund for a cancellable booking."""


class DefaultCancellationPolicy:
    """Calendar-day policy used by the assessment.

    Two or more days before check-in receives 100%, one day receives 50%, and
    cancellation on check-in day receives no refund. Cancellation after check-in
    is rejected. Pending bookings release inventory without a refund.
    """

    def quote(self, booking: Booking, on_date: date) -> RefundQuote:
        if booking.status not in {BookingStatus.PENDING_PAYMENT, BookingStatus.CONFIRMED}:
            raise CancellationNotAllowedError(details={"booking_status": booking.status.value})
        days_before_check_in = (booking.stay.check_in - on_date).days
        if days_before_check_in < 0:
            raise CancellationNotAllowedError("A booking cannot be cancelled after check-in.")
        if booking.status is BookingStatus.PENDING_PAYMENT:
            return RefundQuote(
                amount=Money.zero(booking.total_price.currency),
                percentage=Decimal("0"),
                status=RefundStatus.NOT_REQUIRED,
            )
        if days_before_check_in >= 2:
            percentage = Decimal("100")
        elif days_before_check_in == 1:
            percentage = Decimal("50")
        else:
            percentage = Decimal("0")
        return RefundQuote(
            amount=booking.total_price.percentage(percentage),
            percentage=percentage,
            status=(RefundStatus.CALCULATED if percentage > 0 else RefundStatus.NOT_REQUIRED),
        )
