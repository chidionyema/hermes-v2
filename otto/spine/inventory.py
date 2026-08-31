"""The signed capability inventory (spec §15, §17 Phase 0: "capability
inventory generator" ships before anything else). "A capability not in
the inventory does not exist; a diff without an approved PR is an
incident" — so the artifact has to be generated from the actual config
(never hand-typed) and signed, so a tampered or stale copy is detectable
by anyone holding the public key, not just by whoever built it.

Scope of this checkpoint: CP1 owns the bus (streams, subjects) and its
own module set, so that is what this generator inventories today. The
tool registry, credential handles, ServiceAccounts and egress domains the
full spec §15 describes belong to the tool gateway (CP2) and the sandbox
(Phase 1); this module's `Component` list is exactly what CP1 can attest
to without inventing config that lives in another checkpoint's code. A
later checkpoint extends `_components()` — the signing, diffing and CLI
plumbing built here does not change.

Key handling: Ed25519 via `cryptography` (already an estate-wide pinned
dependency, `hermes-agent/pyproject.toml:96`) — not a new dependency, and
`pynacl` (also already pinned there) was rejected for this one job only
because `cryptography`'s Ed25519 classes are already used estate-wide
for this exact primitive, so introducing a second implementation of the
same signature scheme would be the stitching LAW 43 refuses. Production
key custody is Phase 1's decided secrets backend (OCI Vault via
ExternalSecrets, `PLAN-OPTIMISED.md` decision 2) — this checkpoint's
`load_or_create_keypair` is the local/dev fallback and says so.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from otto.spine import subjects

SPINE_VERSION = "0.1.0-cp1"
KEY_ID = "otto-spine-inventory-2026-08"

_TRACKED_PACKAGES = ("nats-py", "pydantic", "asyncpg", "python-ulid", "cryptography")


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class Component:
    kind: str  # "stream" | "subject_taxonomy" | "python_package" | "module"
    name: str
    version: str
    detail: str = ""


def _components() -> list[Component]:
    out: list[Component] = [
        Component(kind="module", name="otto.spine", version=SPINE_VERSION),
        Component(
            kind="subject_taxonomy",
            name="otto.*.v1.>",
            version="v1",
            detail=f"root wildcard {subjects.ROOT_WILDCARD}",
        ),
    ]
    for spec in subjects.STREAMS:
        out.append(
            Component(
                kind="stream",
                name=spec.name.value,
                version="v1",
                detail=f"subjects={list(spec.subjects)} retention_days={spec.retention_days}",
            )
        )
    for pkg in _TRACKED_PACKAGES:
        try:
            ver = importlib_metadata.version(pkg)
        except importlib_metadata.PackageNotFoundError:
            ver = "ABSENT"
        out.append(Component(kind="python_package", name=pkg, version=ver))
    return sorted(out, key=lambda c: (c.kind, c.name))


def generate_inventory(*, generated_at: datetime | None = None) -> dict:
    """Never hand-maintained: every row here is read off live config
    (`subjects.STREAMS`) or installed package metadata, not typed."""
    ts = generated_at or datetime.now(timezone.utc)
    return {
        "generated_at": ts.isoformat(),
        "spine_version": SPINE_VERSION,
        "components": [asdict(c) for c in _components()],
    }


def default_key_path() -> Path:
    # LAW 46: no hardcoded home directory. Falls back to a path under the
    # caller's own state dir; production custody is the estate's OCI Vault
    # (see module docstring), never this file, once Phase 1 wires it.
    raw = os.environ.get("OTTO_INVENTORY_KEY_PATH")
    if raw:
        return Path(raw)
    state_dir = Path(os.environ.get("OTTO_STATE_DIR", Path.home() / ".otto" / "cp1"))
    return state_dir / "inventory_ed25519.pem"


def load_or_create_keypair(path: Path | None = None) -> Ed25519PrivateKey:
    """Local/dev fallback only — see module docstring. Generates once,
    persists 0600, reuses thereafter so successive `otto inventory` runs
    verify against the same key rather than silently rotating every call."""
    p = path or default_key_path()
    if p.exists():
        data = p.read_bytes()
        key = serialization.load_pem_private_key(data, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise TypeError(f"{p}: not an Ed25519 private key")
        return key
    p.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    p.write_bytes(pem)
    os.chmod(p, 0o600)
    return key


def sign_inventory(
    inventory: dict, private_key: Ed25519PrivateKey, *, key_id: str = KEY_ID
) -> dict:
    sig = private_key.sign(_canonical(inventory))
    return {
        "inventory": inventory,
        "key_id": key_id,
        "signature": base64.b64encode(sig).decode("ascii"),
    }


def verify_signed_inventory(signed: dict, public_key: Ed25519PublicKey) -> bool:
    try:
        sig = base64.b64decode(signed["signature"])
        public_key.verify(sig, _canonical(signed["inventory"]))
        return True
    except (InvalidSignature, KeyError, ValueError):
        return False


def diff_inventory(previous: dict | None, current: dict) -> dict:
    """A row-level diff keyed on (kind, name) — added, removed, changed —
    which is what "a diff without an approved PR is an incident" (§15)
    needs to be actionable: not "the inventory changed" but which rows."""
    prev_rows = {
        (c["kind"], c["name"]): c["version"]
        for c in (previous or {}).get("components", [])
    }
    curr_rows = {
        (c["kind"], c["name"]): c["version"] for c in current.get("components", [])
    }

    added = sorted(k for k in curr_rows if k not in prev_rows)
    removed = sorted(k for k in prev_rows if k not in curr_rows)
    changed = sorted(
        k for k in curr_rows if k in prev_rows and prev_rows[k] != curr_rows[k]
    )
    return {
        "added": [
            {"kind": k, "name": n, "version": curr_rows[(k, n)]} for k, n in added
        ],
        "removed": [
            {"kind": k, "name": n, "version": prev_rows[(k, n)]} for k, n in removed
        ],
        "changed": [
            {"kind": k, "name": n, "from": prev_rows[(k, n)], "to": curr_rows[(k, n)]}
            for k, n in changed
        ],
    }


def inventory_cli(
    *,
    key_path: Path | None = None,
    previous_path: Path | None = None,
    output_path: Path | None = None,
) -> int:
    """`otto inventory --verify-signature`. Generates, signs, verifies its
    own signature (proving the key round-trips, not just that a signature
    string exists), and — when a previous artifact is on disk — prints
    the diff against it. Exit 0 on a valid signature, 1 otherwise.

    `output_path`, when given, persists the signed artifact so the next
    CI run can pass it back in as `--previous` — this is what makes "a
    diff against the previous deploy's inventory is attached to the CI
    run" (spec §15) a real file on disk rather than a claim in a log
    line, e.g. a CI job artifact uploaded from this exact path.
    """
    key = load_or_create_keypair(key_path)
    inventory = generate_inventory()
    signed = sign_inventory(inventory, key)

    ok = verify_signed_inventory(signed, key.public_key())
    print(f"components: {len(inventory['components'])}")
    print(f"key_id: {signed['key_id']}")
    print(f"signature_valid: {ok}")

    prev = None
    if previous_path is not None and previous_path.exists():
        prev_signed = json.loads(previous_path.read_text())
        # Fail closed: an unverifiable --previous is refused rather than
        # silently diffed against. Diffing a tampered artifact would print
        # a false "nothing changed" for a row someone edited by hand after
        # signing — exactly the incident spec §15 exists to catch.
        if not verify_signed_inventory(prev_signed, key.public_key()):
            print(
                f"error: {previous_path} does not verify against this key "
                f"({default_key_path() if key_path is None else key_path}) — refusing to diff",
                file=sys.stderr,
            )
            return 1
        prev = prev_signed.get("inventory")
    diff = diff_inventory(prev, inventory)
    print(
        f"diff: +{len(diff['added'])} -{len(diff['removed'])} ~{len(diff['changed'])}"
    )

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(signed, sort_keys=True, indent=2))
        print(f"written: {output_path}")

    return 0 if ok else 1
