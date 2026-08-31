"""ULID generation (spec section 3: the task ULID doubles as the trace id).

Standard 26-character Crockford base32 ULID: 48-bit millisecond timestamp
plus 80 bits of randomness. No third-party dependency.
"""

from __future__ import annotations

import secrets
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid() -> str:
    """Return one 26-character ULID."""
    value = (int(time.time() * 1000) & ((1 << 48) - 1)) << 80
    value |= secrets.randbits(80)
    chars = []
    for shift in range(125, -1, -5):
        chars.append(_CROCKFORD[(value >> shift) & 0x1F])
    return "".join(chars)


def is_ulid(text: str) -> bool:
    """True when ``text`` is a well-formed ULID."""
    return len(text) == 26 and all(c in _CROCKFORD for c in text)
