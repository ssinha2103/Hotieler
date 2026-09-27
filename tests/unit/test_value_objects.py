from datetime import date
from decimal import Decimal, Inexact, InvalidOperation, localcontext

import pytest

from hotieler.domain.errors import DomainValidationError
from hotieler.domain.value_objects import Money, StayPeriod


def test_money_uses_decimal_and_rounds_to_minor_units() -> None:
    assert Money(Decimal("10.125")).amount == Decimal("10.13")
    assert Money(Decimal("10.124")).amount == Decimal("10.12")
    assert Money.zero("usd") == Money(Decimal("0"), "USD")


def test_money_rejects_value_that_cannot_be_converted_to_decimal() -> None:
    with pytest.raises(DomainValidationError, match="valid decimal") as captured:
        Money("not-a-number")

    assert isinstance(captured.value.__cause__, InvalidOperation)


def test_money_arithmetic_preserves_currency() -> None:
    price = Money(Decimal("1200.50"), "inr")

    assert price.multiply(2) == Money(Decimal("2401.00"), "INR")
    assert price.percentage(50) == Money(Decimal("600.25"), "INR")
    assert price + Money(Decimal("99.50")) == Money(Decimal("1300.00"))
    assert price - Money(Decimal("200.50")) == Money(Decimal("1000.00"))


def test_money_arithmetic_returns_not_implemented_for_other_types() -> None:
    price = Money(Decimal("10"))
    unrelated = object()

    assert price.__add__(unrelated) is NotImplemented
    assert price.__sub__(unrelated) is NotImplemented


def test_money_subtraction_enforces_currency_and_non_negative_result() -> None:
    price = Money(Decimal("100"), "INR")

    with pytest.raises(DomainValidationError, match="same currency"):
        _ = price - Money(Decimal("1"), "USD")
    with pytest.raises(DomainValidationError, match="cannot produce a negative"):
        _ = price - Money(Decimal("100.01"), "INR")


def test_money_rejects_negative_multiplier() -> None:
    with pytest.raises(DomainValidationError, match="multiplier cannot be negative"):
        Money(Decimal("10")).multiply(Decimal("-0.01"))


@pytest.mark.parametrize("percentage", [Decimal("-0.01"), Decimal("100.01")])
def test_money_rejects_out_of_range_percentage(percentage: Decimal) -> None:
    with pytest.raises(DomainValidationError, match="Percentage must be between 0 and 100"):
        Money(Decimal("10")).percentage(percentage)


def test_money_rejects_negative_non_finite_and_currency_mismatch() -> None:
    with pytest.raises(DomainValidationError):
        Money(Decimal("-0.01"))
    with pytest.raises(DomainValidationError, match="cannot be negative"):
        Money(Decimal("-0.001"))
    with pytest.raises(DomainValidationError):
        Money(Decimal("NaN"))
    with pytest.raises(DomainValidationError):
        Money(Decimal("1"), "RUPEES")
    with pytest.raises(DomainValidationError):
        _ = Money(Decimal("1"), "INR") + Money(Decimal("1"), "USD")


def test_money_converts_quantize_failure_to_domain_validation_error() -> None:
    with pytest.raises(
        DomainValidationError,
        match="unsupported precision or magnitude",
    ) as captured:
        Money(Decimal("1e28"))

    assert isinstance(captured.value.__cause__, InvalidOperation)


def test_money_rejects_non_finite_quantize_result_when_decimal_trap_is_disabled() -> None:
    with localcontext() as context:
        context.prec = 1
        context.traps[InvalidOperation] = False
        with pytest.raises(
            DomainValidationError,
            match="unsupported precision or magnitude",
        ):
            Money(Decimal("99.99"))


def test_money_converts_arithmetic_decimal_failure_to_domain_validation_error() -> None:
    price = Money(Decimal("99.99"))

    with localcontext() as context:
        context.prec = 3
        context.traps[Inexact] = True
        with pytest.raises(DomainValidationError, match="multiplication") as captured:
            price.multiply(Decimal("1.1"))

    assert isinstance(captured.value.__cause__, Inexact)


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


def test_stay_period_accepts_check_in_today_or_later() -> None:
    today = date(2030, 1, 1)

    StayPeriod(today, date(2030, 1, 2)).ensure_not_in_past(today)
    StayPeriod(date(2030, 1, 2), date(2030, 1, 3)).ensure_not_in_past(today)
