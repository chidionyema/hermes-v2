"""Otto's transport adapter for the *one* idp#8478 founder admission authority.

No policy is reimplemented here. A trusted off-process producer provides a
fresh, signed per-task certificate; independent observers provide its immutable
evidence; the idp PEP checks it immediately before the real channel send.
Unconfigured, stale, unsigned or conflicting input causes an explicit denial.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class FounderProofUnavailable(ValueError):
    """Conserve the original NATS task; never send an unproved status."""


_TASK_ID = re.compile(r"[0-9A-Z]{26}\Z")
_MAX_BYTES = 1024 * 1024
_MAX_MESSAGE_BYTES = 65536


def _read(path: Path, *, limit: int = _MAX_BYTES) -> bytes:
    # Trust paths are mounted by the operator, never chosen by a model reply.
    if not path.is_file() or path.is_symlink():
        raise FounderProofUnavailable("trusted-material-missing-or-link")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if not data or len(data) > limit:
        raise FounderProofUnavailable("trusted-material-empty-or-oversized")
    return data


def _json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(_read(path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise FounderProofUnavailable("unreadable-trusted-material") from exc
    if not isinstance(data, dict):
        raise FounderProofUnavailable("invalid-signed-bundle")
    return data


def send_certified(
    *,
    task_id: str,
    channel: str,
    reply_to: str,
    delivery_secret: str,
    plugin: Any,
    now: datetime | None = None,
    on_denial: Callable[[dict], object] | None = None,
) -> object:
    """Send only the exact pre-certified status bytes, without model inference.

    Distinct evidence collectors and certifier control planes must provision
    fresh signed payloads. Public trust roots live at a separate read-only
    mount; a model's bundled 'trusted_roots' are NEVER considered authority.
    """
    # The verifier source is pinned by Dockerfile to a protected idp commit.
    # A missing pinned module is not a reason to send unchecked.
    try:
        from lib.founder_admission import send_checked
    except ImportError as exc:
        raise FounderProofUnavailable("pinned-founder-verifier-not-installed") from exc

    if not _TASK_ID.fullmatch(task_id) or not reply_to or not channel:
        raise FounderProofUnavailable("invalid-task-recipient-binding")
    root_dir = os.environ.get("OTTO_FOUNDER_CERT_DIR", "")
    roots_path = os.environ.get("OTTO_FOUNDER_TRUST_ROOTS_FILE", "")
    if not root_dir or not roots_path:
        raise FounderProofUnavailable("independent-evidence-custody-unconfigured")
    task_dir = Path(root_dir) / task_id
    bundle = _json_file(task_dir / "certificate.json")
    trust_roots = _json_file(Path(roots_path))
    claim = bundle.get("claim")
    discovery = bundle.get("discovery")
    proofs = bundle.get("proofs")
    if not isinstance(claim, dict) or not isinstance(discovery, dict) or not isinstance(proofs, list):
        raise FounderProofUnavailable("incomplete-signed-certificate")
    try:
        payload = base64.b64decode(bundle["payload_b64"], validate=True)
        original = base64.b64decode(bundle["original_instruction_b64"], validate=True)
    except (KeyError, TypeError, binascii.Error, ValueError) as exc:
        raise FounderProofUnavailable("immutable-payload-or-founder-instruction-missing") from exc
    if not payload or len(payload) > _MAX_MESSAGE_BYTES or not original:
        raise FounderProofUnavailable("empty-or-oversized-certified-payload")
    if (
        claim.get("task_id") != task_id
        or claim.get("channel") != channel
        or claim.get("recipient") != reply_to
        or claim.get("outbound_payload_sha256") != hashlib.sha256(payload).hexdigest()
    ):
        raise FounderProofUnavailable("outbound-message-binding-mismatch")
    claim = dict(claim, original_instruction_bytes=original)

    def read_evidence(uri: str) -> bytes:
        # URI is only an identity for separately captured bytes in the mounted
        # immutable evidence store, not an SSRF target or arbitrary local path.
        if not isinstance(uri, str) or not uri.startswith("https://"):
            raise FounderProofUnavailable("invalid-evidence-uri")
        name = hashlib.sha256(uri.encode("utf-8")).hexdigest()
        return _read(task_dir / "evidence" / name)

    def transmit(raw: bytes) -> object:
        # Never let output bytes bypass the exact digest the certifier signed.
        if raw != payload:
            raise FounderProofUnavailable("outbound-bytes-changed-after-verification")
        return plugin.send_reply(delivery_secret, reply_to, raw.decode("utf-8"))

    if not callable(on_denial):
        raise FounderProofUnavailable("durable-obligation-conservation-missing")
    return send_checked(
        transmit,
        payload,
        claim,
        discovery,
        proofs,
        now=now or datetime.now(timezone.utc),
        trust_roots=trust_roots,
        read_evidence=read_evidence,
        conserve_obligation=on_denial,
    )
