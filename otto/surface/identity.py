"""The no-voiceprint rule (spec bullet 4): voice NEVER authenticates.

``SurfaceEnvelope.principal`` may only be resolved from a surface's bound
identity — a Telegram account bound in an allow-list, or a passkey-bound
device. A binding that wants to set ``principal`` from anything else,
today or in a later lane, must call ``validate_principal_source`` first;
this is the one place the rule is coded, so no later lane can ship voice
authentication without a test — this module's own — already refusing it.
"""

from __future__ import annotations

BOUND_ACCOUNT = "bound_account"
PASSKEY_DEVICE = "passkey_device"

ALLOWED_PRINCIPAL_SOURCES = frozenset({BOUND_ACCOUNT, PASSKEY_DEVICE})

# Named explicitly (spec bullet 4: "a voice-auth attempt (principal
# claimed from audio) is refused by validation") rather than derived as
# "everything not in ALLOWED_PRINCIPAL_SOURCES", so the refusal message
# below can name the specific audio/biometric signal instead of a bare
# "not allowed".
REFUSED_PRINCIPAL_SOURCES = frozenset({"voice", "audio", "biometric"})


def validate_principal_source(principal_source: str) -> None:
    """Raise ``ValueError`` if ``principal_source`` claims to derive a
    principal from an audio or biometric signal, or from any source not
    on the allow-list. Returns ``None`` (no exception) for an allowed
    source. A binding calls this before assigning
    ``SurfaceEnvelope.principal`` from any claim it did not itself
    resolve from a static allow-list it was configured with.
    """
    if principal_source in REFUSED_PRINCIPAL_SOURCES:
        raise ValueError(
            f"principal_source={principal_source!r} refused: voice never "
            "authenticates a principal (no-voiceprint rule, "
            "SURFACE-CONTRACT-DAY0.md bullet 4)"
        )
    if principal_source not in ALLOWED_PRINCIPAL_SOURCES:
        raise ValueError(
            f"principal_source={principal_source!r} is not a recognised "
            f"principal source; allowed: {sorted(ALLOWED_PRINCIPAL_SOURCES)}"
        )
