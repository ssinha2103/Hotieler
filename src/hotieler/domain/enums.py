"""Enumerations used by the hotel booking domain."""

from enum import StrEnum


class BookingStatus(StrEnum):
    """Lifecycle states for a booking."""

    PENDING_PAYMENT = "PENDING_PAYMENT"
    CONFIRMED = "CONFIRMED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    CANCELLED = "CANCELLED"


class PaymentMethod(StrEnum):
    """Payment methods supported by the deterministic demo gateway."""

    CARD = "CARD"
    UPI = "UPI"
    WALLET = "WALLET"


class MockPaymentOutcome(StrEnum):
    """Explicit payment outcome requested from the mock processor."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PaymentStatus(StrEnum):
    """Persisted status of a payment attempt."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RefundStatus(StrEnum):
    """Result of applying the assessment's synchronous refund policy."""

    NOT_REQUIRED = "NOT_REQUIRED"
    CALCULATED = "CALCULATED"
