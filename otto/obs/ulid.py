"""ULID handling (spec section 3: the task ULID doubles as the trace id).

Standard 26-character Crockford base32 ULID: 48-bit millisecond timestamp
plus 80 bits of randomness. No third-party dependency; byte-compatible
with ``otto.router.ulid`` so the integration wave keeps one convention.
"""

from __future__ import annotations

import secrets
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {c: i for i, c in enumerate(_CROCKFORD)}


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


def ulid_to_trace_id(ulid: str) -> int:
    """The ULID's 128 bits as an OpenTelemetry trace id.

    Spec section 3 makes the task ULID double as the trace id: decode the
    Crockford base32 back to its 128-bit integer. A zero trace id is
    invalid in OpenTelemetry, so the (practically unreachable) all-zero
    ULID maps to 1 rather than to an invalid id.
    """
    if not is_ulid(ulid):
        raise ValueError(f"not a ULID: {ulid!r}")
    value = 0
    for char in ulid:
        value = (value << 5) | _DECODE[char]
    value &= (1 << 128) - 1
    return value or 1
