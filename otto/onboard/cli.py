"""``otto onboard <service>`` — the command itself.

Reachable two ways, same code path:

- ``python -m otto.spine.cli onboard <service>`` (the branch's ``otto``
  dispatcher, extended minimally to delegate here);
- ``python -m otto.onboard <service>`` (this package standalone).

The manifest path is explicit, never guessed: ``--manifest`` wins, else
``OTTO_ONBOARD_MANIFEST_DIR/<service>.yaml`` (LAW 46 — the location comes
from the environment, not from this file), else a structured refusal.
Output is one JSON line — the green outcome or the refusal — and the
exit code is 0 only on a green, fully admitted onboarding.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from otto.onboard.core import onboard_service
from otto.onboard.errors import OnboardingRefused
from otto.onboard.manifest import load_manifest

MANIFEST_DIR_ENV = "OTTO_ONBOARD_MANIFEST_DIR"


def _resolve_manifest_path(service: str, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    raw = os.environ.get(MANIFEST_DIR_ENV)
    if raw:
        return Path(raw) / f"{service}.yaml"
    raise OnboardingRefused(
        service,
        "load_manifest",
        "no manifest path was given and no manifest directory is "
        "configured, so there is nothing to onboard from",
        f"pass --manifest <path>, or set {MANIFEST_DIR_ENV} to the "
        "directory holding <service>.yaml manifests",
    )


def onboard_command(
    *,
    service: str,
    manifest_path: Path | None = None,
    output_dir: Path | None = None,
    key_path: Path | None = None,
    registry=None,
    trace_backend=None,
    stdout=None,
) -> int:
    """Run the onboarding; print one JSON line; exit 0 only on green.

    ``registry`` and ``trace_backend`` are the test seams, mirroring how
    ``otto.obs.coverage.main`` takes its backend.
    """
    out = stdout if stdout is not None else sys.stdout
    try:
        manifest = load_manifest(_resolve_manifest_path(service, manifest_path))
        if manifest.service != service:
            raise OnboardingRefused(
                service,
                "load_manifest",
                f"the manifest names service {manifest.service!r} but the "
                f"command asked to onboard {service!r}; refusing the mismatch",
                "onboard the service the manifest actually names",
            )
        outcome = onboard_service(
            manifest,
            registry=registry,
            output_dir=output_dir,
            key_path=key_path,
            trace_backend=trace_backend,
        )
    except OnboardingRefused as exc:
        print(json.dumps(exc.as_dict()), file=out)
        print(
            f"otto onboard: {service} was NOT admitted — {exc.step}: {exc.reason}",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(outcome.as_dict()), file=out)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="otto onboard",
        description="Onboard an estate service onto Otto. Onboarding is "
        "the admission ticket: a service that is not onboarded is not "
        "admitted.",
    )
    parser.add_argument("service", help="the service name the manifest declares")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="path to the service's onboarding manifest YAML "
        f"(default: {MANIFEST_DIR_ENV}/<service>.yaml)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="where the catalog entity and signed inventory land "
        "(default: OTTO_ONBOARD_DIR, then the estate state dir)",
    )
    parser.add_argument(
        "--key-path",
        type=Path,
        default=None,
        help="override the Ed25519 key path (default: the inventory "
        "machinery's own env/LAW46 ladder)",
    )
    args = parser.parse_args(argv)
    return onboard_command(
        service=args.service,
        manifest_path=args.manifest,
        output_dir=args.output_dir,
        key_path=args.key_path,
    )
