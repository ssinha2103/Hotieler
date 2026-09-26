"""Process-local keyed locking.

The application intentionally stores state in one process.  A keyed lock keeps
unrelated room types and bookings independent while serialising operations that
must make a read/check/write decision against the same aggregate.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock, RLock


@dataclass(slots=True)
class _LockEntry:
    lock: RLock
    references: int = 0


class InMemoryKeyedLockManager:
    """Reference-counted registry of re-entrant locks.

    References include both lock holders and waiters.  This prevents an entry
    from being removed while another thread is waiting on its lock and lets the
    registry reclaim keys once the final user exits the context manager.
    """

    def __init__(self) -> None:
        self._registry_lock = Lock()
        self._entries: dict[str, _LockEntry] = {}

    @contextmanager
    def lock(self, key: str) -> Iterator[None]:
        if not key or not key.strip():
            raise ValueError("Lock key must not be blank.")

        with self._registry_lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = _LockEntry(lock=RLock())
                self._entries[key] = entry
            entry.references += 1

        entry.lock.acquire()
        try:
            yield
        finally:
            entry.lock.release()
            with self._registry_lock:
                entry.references -= 1
                if entry.references == 0:
                    self._entries.pop(key, None)

    @property
    def active_key_count(self) -> int:
        """Expose registry size for focused lifecycle tests."""

        with self._registry_lock:
            return len(self._entries)
