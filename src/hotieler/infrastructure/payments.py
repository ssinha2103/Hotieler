"""Deterministic payment adapters used by the assessment application."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from threading import Lock
from uuid import UUID

from hotieler.application.models import PaymentProcessorResult
from hotieler.application.ports import Clock, PaymentProcessor
from hotieler.domain.enums import MockPaymentOutcome, PaymentMethod, PaymentStatus
from hotieler.domain.value_objects import Money


class DeterministicPaymentProcessor:
    """A credential-free processor with an explicit caller-selected outcome."""

    def __init__(self, method: PaymentMethod, clock: Clock) -> None:
        self._method = method
        self._clock = clock
        self._process_count = 0
        self._counter_lock = Lock()

    def process(
        self,
        booking_id: UUID,
        amount: Money,
        mock_outcome: MockPaymentOutcome,
    ) -> PaymentProcessorResult:
        with self._counter_lock:
            self._process_count += 1

        status = (
            PaymentStatus.APPROVED
            if mock_outcome is MockPaymentOutcome.APPROVED
            else PaymentStatus.REJECTED
        )
        reference_source = (
            f"{self._method.value}:{booking_id}:{amount.currency}:"
            f"{amount.amount}:{mock_outcome.value}"
        )
        provider_reference = f"MOCK-{self._method.value}-{sha256(reference_source.encode()).hexdigest()[:16].upper()}"
        return PaymentProcessorResult(
            status=status,
            provider_reference=provider_reference,
            processed_at=self._clock.now(),
        )

    @property
    def process_count(self) -> int:
        with self._counter_lock:
            return self._process_count


def build_payment_processors(clock: Clock) -> Mapping[PaymentMethod, PaymentProcessor]:
    """Compose one deterministic adapter for each supported payment method."""

    return {
        method: DeterministicPaymentProcessor(method=method, clock=clock)
        for method in PaymentMethod
    }
