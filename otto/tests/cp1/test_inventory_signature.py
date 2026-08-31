"""Fail-closed proof for `otto inventory --previous`. Not a Gherkin
scenario (the feature file covers the spec's own acceptance scenarios,
not every unit-level regression) — this is the regression test for the
tampered-previous-inventory defect the independent verifier filed against
crew#768 CP1: `inventory_cli` used to diff against `--previous` without
ever checking its signature, so a hand-edited "previous deploy" artifact
would produce a false "nothing changed" diff instead of being refused.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from otto.spine import inventory as inventory_mod


@pytest.fixture
def key_path(tmp_path: Path) -> Path:
    return tmp_path / "key.pem"


def _write_signed_inventory(key_path: Path, out_path: Path) -> None:
    key = inventory_mod.load_or_create_keypair(key_path)
    inv = inventory_mod.generate_inventory()
    signed = inventory_mod.sign_inventory(inv, key)
    out_path.write_text(json.dumps(signed))


def test_a_genuine_previous_inventory_is_accepted(
    tmp_path: Path, key_path: Path
) -> None:
    previous_path = tmp_path / "previous.json"
    _write_signed_inventory(key_path, previous_path)

    exit_code = inventory_mod.inventory_cli(
        key_path=key_path, previous_path=previous_path
    )

    assert exit_code == 0


def test_a_tampered_previous_inventory_is_refused(
    tmp_path: Path, key_path: Path
) -> None:
    previous_path = tmp_path / "previous.json"
    _write_signed_inventory(key_path, previous_path)

    # Tamper with a component's version after signing, exactly the "someone
    # hand-edited the artifact after it was signed" case spec §15 exists to
    # catch — the signature on disk no longer matches the payload.
    signed = json.loads(previous_path.read_text())
    signed["inventory"]["components"][0]["version"] = "9.9.9-tampered"
    previous_path.write_text(json.dumps(signed))

    exit_code = inventory_mod.inventory_cli(
        key_path=key_path, previous_path=previous_path
    )

    assert exit_code != 0, "a tampered --previous must be refused, not silently diffed"


def test_a_previous_inventory_signed_by_a_different_key_is_refused(
    tmp_path: Path, key_path: Path
) -> None:
    other_key_path = tmp_path / "other-key.pem"
    previous_path = tmp_path / "previous.json"
    _write_signed_inventory(other_key_path, previous_path)

    exit_code = inventory_mod.inventory_cli(
        key_path=key_path, previous_path=previous_path
    )

    assert exit_code != 0
