"""Runtime and deterministic time/identifier adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from uuid import UUID, uuid4, uuid5


class SystemClock:
    """Return timezone-aware UTC timestamps."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(slots=True)
class FixedClock:
    """Clock used by tests and deterministic demonstrations."""

    current: datetime

    def __post_init__(self) -> None:
        if self.current.tzinfo is None or self.current.utcoffset() is None:
            raise ValueError("FixedClock requires a timezone-aware timestamp.")

    def now(self) -> datetime:
        return self.current.astimezone(UTC)

    def set(self, value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("FixedClock requires a timezone-aware timestamp.")
        self.current = value.astimezone(UTC)


class UuidGenerator:
    def new(self) -> UUID:
        return uuid4()


class DeterministicIdGenerator:
    """Thread-safe repeatable UUID generator for tests."""

    def __init__(self, namespace: UUID, prefix: str = "hotieler") -> None:
        self._namespace = namespace
        self._prefix = prefix
        self._counter = 0
        self._lock = Lock()

    def new(self) -> UUID:
        with self._lock:
            self._counter += 1
            sequence = self._counter
        return uuid5(self._namespace, f"{self._prefix}:{sequence}")
