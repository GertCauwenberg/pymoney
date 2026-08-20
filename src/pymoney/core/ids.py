"""UUIDv7 generation (decision #5).

Python 3.12 has no ``uuid.uuid7``, so we implement RFC 9562 here.  The layout
is a 48-bit millisecond timestamp, the version nibble, 12 bits we use as a
sub-millisecond counter (RFC 9562 "method 1"), the variant bits, and 62 random
bits.

The counter is what makes the ids *monotonic*, not merely time-ordered: two ids
minted in the same millisecond still sort in creation order.  That is the whole
reason to prefer v7 over v4 here -- primary keys stay locality-friendly in
B-trees, and the change log sorts naturally by id.

The generator keeps state (last timestamp, counter), so it lives in a class.  A
process-wide default instance backs the module-level :func:`uuid7` and
:func:`new_id`; tests and importers that need determinism can create their own.
"""

from __future__ import annotations

import os
import threading
import time
from uuid import UUID

__all__ = [
    "Uuid7Generator",
    "counter_of",
    "is_uuid7",
    "new_id",
    "timestamp_ms_of",
    "uuid7",
]

_MAX_COUNTER = 0xFFF  # 12 bits of rand_a, used as a sub-millisecond counter


class Uuid7Generator:
    """Thread-safe, monotonic UUIDv7 generator."""

    __slots__ = ("_counter", "_last_ms", "_lock", "_randbytes")

    def __init__(self, randbytes=None) -> None:
        self._lock = threading.Lock()
        self._last_ms = -1
        self._counter = 0
        self._randbytes = randbytes or os.urandom

    def __call__(self, ts_ms: int | None = None) -> UUID:
        """Mint the next id.

        :param ts_ms: Unix timestamp in milliseconds; defaults to "now".  It is
            a *hint*: a value at or below the last one used is clamped forward,
            because monotonicity matters more than honouring a backwards clock
            (an NTP step must not produce ids that sort before existing rows).
        """
        with self._lock:
            now_ms = int(time.time() * 1000) if ts_ms is None else int(ts_ms)
            if now_ms > self._last_ms:
                self._last_ms, self._counter = now_ms, 0
            else:
                # Same millisecond, or the clock went backwards: keep counting.
                self._counter += 1
                if self._counter > _MAX_COUNTER:
                    self._last_ms += 1
                    self._counter = 0
                now_ms = self._last_ms
            counter = self._counter

        rand_b = int.from_bytes(self._randbytes(8), "big") & ((1 << 62) - 1)
        value = (now_ms & ((1 << 48) - 1)) << 80
        value |= 0x7 << 76
        value |= counter << 64
        value |= 0b10 << 62
        value |= rand_b
        return UUID(int=value)


_default = Uuid7Generator()


def uuid7(ts_ms: int | None = None) -> UUID:
    """Return a new monotonic UUIDv7 from the process-wide generator."""
    return _default(ts_ms)


def new_id() -> UUID:
    """Alias used throughout the domain, so the id strategy stays swappable."""
    return _default(None)


def timestamp_ms_of(value: UUID) -> int:
    """Extract the embedded millisecond timestamp from a UUIDv7."""
    if value.version != 7:
        raise ValueError(f"not a UUIDv7: {value}")
    return value.int >> 80


def counter_of(value: UUID) -> int:
    """Extract the sub-millisecond counter from a UUIDv7."""
    if value.version != 7:
        raise ValueError(f"not a UUIDv7: {value}")
    return (value.int >> 64) & _MAX_COUNTER


def is_uuid7(value: UUID) -> bool:
    return value.version == 7
