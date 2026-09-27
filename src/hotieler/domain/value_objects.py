"""Immutable value objects and their invariants."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, DecimalException
from typing import cast

from hotieler.domain.errors import DomainValidationError

_CENT = Decimal("0.01")


def effective_decimal_places(value: Decimal) -> int:
    """Return scale after insignificant trailing zeroes have been removed."""

    if value.is_zero():
        return 0
    decimal_tuple = value.as_tuple()
    exponent = cast(int, decimal_tuple.exponent)
    coefficient = "".join(str(digit) for digit in decimal_tuple.digits)
    trailing_zeroes = len(coefficient) - len(coefficient.rstrip("0"))
    return max(0, -exponent - trailing_zeroes)


def _decimal(value: Decimal | int | str) -> Decimal:
    try:
        candidate = value if isinstance(value, Decimal) else Decimal(str(value))
    except (DecimalException, ValueError) as exc:
        raise DomainValidationError("Money amount must be a valid decimal.") from exc
    if not candidate.is_finite():
        raise DomainValidationError("Money amount must be finite.")
    return candidate


def _decimal_operation(operation: Callable[[], Decimal], message: str) -> Decimal:
    try:
        return operation()
    except DecimalException as exc:
        raise DomainValidationError(message) from exc


@dataclass(frozen=True, slots=True)
class Money:
    """A non-negative amount in one ISO-style currency.

    Values are quantized to two fractional digits because this assessment models
    hotel prices rather than arbitrary-precision financial instruments.
    """

    amount: Decimal
    currency: str = "INR"

    def __post_init__(self) -> None:
        raw_amount = _decimal(self.amount)
        if raw_amount < 0:
            raise DomainValidationError("Money amount cannot be negative.")
        amount = _decimal_operation(
            lambda: raw_amount.quantize(_CENT, rounding=ROUND_HALF_UP),
            "Money amount has unsupported precision or magnitude.",
        )
        if not amount.is_finite():
            raise DomainValidationError("Money amount has unsupported precision or magnitude.")
        currency = self.currency.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise DomainValidationError("Currency must be a three-letter code.")
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "currency", currency)

    @classmethod
    def zero(cls, currency: str = "INR") -> "Money":
        return cls(Decimal("0"), currency)

    def require_same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise DomainValidationError(
                "Money values must use the same currency.",
                details={"left": self.currency, "right": other.currency},
            )

    def __add__(self, other: "Money") -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        self.require_same_currency(other)
        amount = _decimal_operation(
            lambda: self.amount + other.amount,
            "Money addition could not be completed.",
        )
        return Money(amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        self.require_same_currency(other)
        if other.amount > self.amount:
            raise DomainValidationError("Money subtraction cannot produce a negative amount.")
        amount = _decimal_operation(
            lambda: self.amount - other.amount,
            "Money subtraction could not be completed.",
        )
        return Money(amount, self.currency)

    def multiply(self, multiplier: Decimal | int | str) -> "Money":
        factor = _decimal(multiplier)
        if factor < 0:
            raise DomainValidationError("Money multiplier cannot be negative.")
        amount = _decimal_operation(
            lambda: self.amount * factor,
            "Money multiplication could not be completed.",
        )
        return Money(amount, self.currency)

    def percentage(self, percent: Decimal | int | str) -> "Money":
        value = _decimal(percent)
        if value < 0 or value > 100:
            raise DomainValidationError("Percentage must be between 0 and 100.")
        factor = _decimal_operation(
            lambda: value / Decimal("100"),
            "Money percentage calculation could not be completed.",
        )
        return self.multiply(factor)


@dataclass(frozen=True, slots=True)
class StayPeriod:
    """A half-open hotel stay interval: ``[check_in, check_out)``."""

    check_in: date
    check_out: date

    def __post_init__(self) -> None:
        if self.check_out <= self.check_in:
            raise DomainValidationError("Check-out must be after check-in.")

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days

    def overlaps(self, other: "StayPeriod") -> bool:
        return self.check_in < other.check_out and other.check_in < self.check_out

    def ensure_not_in_past(self, today: date) -> None:
        if self.check_in < today:
            raise DomainValidationError(
                "Check-in cannot be in the past.",
                details={"check_in": self.check_in.isoformat(), "today": today.isoformat()},
            )
