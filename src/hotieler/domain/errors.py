"""Stable error taxonomy shared by the domain and application layers."""

from collections.abc import Mapping
from typing import Any


class HotielerError(Exception):
    """Base class for errors safe to expose through the HTTP boundary."""

    code = "HOTIELER_ERROR"
    default_message = "The request could not be completed."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = dict(details or {})
        super().__init__(self.message)


class DomainValidationError(HotielerError):
    code = "DOMAIN_VALIDATION_ERROR"
    default_message = "The supplied domain data is invalid."


class ResourceNotFoundError(HotielerError):
    code = "RESOURCE_NOT_FOUND"
    default_message = "The requested resource does not exist."


class ConflictError(HotielerError):
    code = "CONFLICT"
    default_message = "The request conflicts with the current state."


class InvalidBookingTransitionError(ConflictError):
    code = "INVALID_BOOKING_TRANSITION"
    default_message = "The booking cannot transition from its current state."


class RoomInventoryUnavailableError(ConflictError):
    code = "ROOM_INVENTORY_UNAVAILABLE"
    default_message = "The requested room inventory is no longer available."


class PropertyRoomMismatchError(ConflictError):
    code = "PROPERTY_ROOM_MISMATCH"
    default_message = "The room type does not belong to the requested property."


class IdempotencyConflictError(ConflictError):
    code = "IDEMPOTENCY_KEY_CONFLICT"
    default_message = "The idempotency key was already used for a different request."


class CancellationNotAllowedError(ConflictError):
    code = "CANCELLATION_NOT_ALLOWED"
    default_message = "The booking can no longer be cancelled."


class UnsupportedPaymentMethodError(DomainValidationError):
    code = "UNSUPPORTED_PAYMENT_METHOD"
    default_message = "The selected payment method is not supported."


class DuplicateResourceError(ConflictError):
    code = "DUPLICATE_RESOURCE"
    default_message = "A resource with this identifier already exists."
