from datetime import date
from decimal import Decimal

import pytest

from hotieler.domain.errors import DomainValidationError
from hotieler.domain.value_objects import Money, StayPeriod


def test_money_uses_decimal_and_rounds_to_minor_units() -> None:
    assert Money(Decimal("10.125")).amount == Decimal("10.13")
    assert Money(Decimal("10.124")).amount == Decimal("10.12")


def test_money_arithmetic_preserves_currency() -> None:
    price = Money(Decimal("1200.50"), "inr")

    assert price.multiply(2) == Money(Decimal("2401.00"), "INR")
    assert price.percentage(50) == Money(Decimal("600.25"), "INR")
    assert price + Money(Decimal("99.50")) == Money(Decimal("1300.00"))


def test_money_rejects_negative_non_finite_and_currency_mismatch() -> None:
    with pytest.raises(DomainValidationError):
        Money(Decimal("-0.01"))
    with pytest.raises(DomainValidationError):
        Money(Decimal("NaN"))
    with pytest.raises(DomainValidationError):
        Money(Decimal("1"), "RUPEES")
    with pytest.raises(DomainValidationError):
        _ = Money(Decimal("1"), "INR") + Money(Decimal("1"), "USD")


def test_stay_period_uses_half_open_overlap_semantics() -> None:
    stay = StayPeriod(date(2030, 1, 10), date(2030, 1, 12))

    assert stay.nights == 2
    assert stay.overlaps(StayPeriod(date(2030, 1, 11), date(2030, 1, 13)))
    assert stay.overlaps(StayPeriod(date(2030, 1, 9), date(2030, 1, 11)))
    assert stay.overlaps(StayPeriod(date(2030, 1, 10), date(2030, 1, 12)))
    assert not stay.overlaps(StayPeriod(date(2030, 1, 12), date(2030, 1, 13)))
    assert not stay.overlaps(StayPeriod(date(2030, 1, 8), date(2030, 1, 10)))


@pytest.mark.parametrize(
    ("check_in", "check_out"),
    [
        (date(2030, 1, 10), date(2030, 1, 10)),
        (date(2030, 1, 11), date(2030, 1, 10)),
    ],
)
def test_stay_period_rejects_zero_or_negative_nights(check_in: date, check_out: date) -> None:
    with pytest.raises(DomainValidationError):
        StayPeriod(check_in, check_out)


def test_stay_period_rejects_past_check_in_against_explicit_clock_date() -> None:
    stay = StayPeriod(date(2029, 12, 31), date(2030, 1, 1))

    with pytest.raises(DomainValidationError):
        stay.ensure_not_in_past(date(2030, 1, 1))
