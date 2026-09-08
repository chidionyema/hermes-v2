"""Regression: the trust gate decides on the value it reads at check time.

An independent verifier probe proved ``object.__setattr__`` still rewrites
a frozen, slotted dataclass field on CPython, so construction-time
validation of ``trust_class`` does not bind a hostile caller. The gate
(``is_instruction_bearing``) must re-validate the value it reads: a
casefold-twin raw string must grade as the class it names, and an unknown
class must be refused, never treated as instruction-bearing.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from otto.surface.envelope import Capability, SurfaceEnvelope, TrustClass


def _envelope(trust_class: TrustClass) -> SurfaceEnvelope:
    return SurfaceEnvelope(
        tenant_id="tenant-under-test",
        surface="test",
        principal=None,
        trust_class=trust_class,
        capabilities=frozenset({Capability.TEXT}),
        content="observation",
        received_at=datetime.now(timezone.utc),
    )


def test_mutated_raw_string_ambient_is_still_not_instruction_bearing() -> None:
    # The raw string "ambient" is not the enum member, so the old identity
    # comparison (``is not TrustClass.AMBIENT``) graded it as
    # instruction-bearing — an ambient envelope promoted to an instruction
    # carrier by one setattr. The gate must grade the value it reads.
    env = _envelope(TrustClass.AMBIENT)
    object.__setattr__(env, "trust_class", "ambient")
    assert env.is_instruction_bearing is False


def test_mutated_unknown_trust_class_is_refused_not_promoted() -> None:
    # Any value outside the three known classes must raise, never fall
    # through to "not ambient, therefore instruction-bearing".
    env = _envelope(TrustClass.OPERATOR)
    object.__setattr__(env, "trust_class", "root")
    with pytest.raises(ValueError, match="unknown trust class"):
        _ = env.is_instruction_bearing


def test_mutated_uppercase_spelling_is_refused() -> None:
    # "AMBIENT" is not a member value; a near-miss spelling is refused,
    # not silently graded as either class.
    env = _envelope(TrustClass.AMBIENT)
    object.__setattr__(env, "trust_class", "AMBIENT")
    with pytest.raises(ValueError, match="unknown trust class"):
        _ = env.is_instruction_bearing


def test_construction_refuses_an_unknown_trust_class() -> None:
    with pytest.raises(ValueError):
        _envelope("root")  # type: ignore[arg-type]


def test_construction_coerces_a_raw_known_string_to_the_enum() -> None:
    env = _envelope("ambient")  # type: ignore[arg-type]
    assert env.trust_class is TrustClass.AMBIENT
    assert env.is_instruction_bearing is False
