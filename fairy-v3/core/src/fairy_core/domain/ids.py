from __future__ import annotations

import secrets
import threading
import time
from uuid import UUID

_lock = threading.Lock()
_last_timestamp_ms = -1
_last_random = 0
_RANDOM_MASK = (1 << 74) - 1


def new_id() -> UUID:
    """Return a monotonic UUIDv7 using RFC 9562 bit placement."""

    global _last_random, _last_timestamp_ms
    with _lock:
        timestamp_ms = int(time.time_ns() // 1_000_000)
        if timestamp_ms > _last_timestamp_ms:
            random_bits = secrets.randbits(74)
        else:
            timestamp_ms = _last_timestamp_ms
            random_bits = (_last_random + 1) & _RANDOM_MASK
            if random_bits == 0:
                timestamp_ms += 1
        _last_timestamp_ms = timestamp_ms
        _last_random = random_bits

    random_a = random_bits >> 62
    random_b = random_bits & ((1 << 62) - 1)
    value = (timestamp_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= random_a << 64
    value |= 0b10 << 62
    value |= random_b
    return UUID(int=value)
