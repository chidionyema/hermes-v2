"""Coverage gate ``otto-obs-coverage`` (LAW 50): query the backend, never files.

Every component named in the input list must have recent spans IN THE
TRACE BACKEND, or the gate is red and the process exits nonzero. Coverage
is proved by querying the backend — a component that "should" emit but is
absent from the backend is a black box, and a black box fails the gate.

The component list is an INPUT, designed to be fed from the signed
capability inventory (CP1's Ed25519-signed inventory is the source of
truth for what exists); it is never hand-kept in this file. The CLI takes
``--components-file`` pointing at a JSON array generated from that
inventory.

The backend binds through the small ``TraceBackend`` Protocol: SigNoz's
query API implements it at integration (W2 — no SigNoz URL lives in this
package); the test suite binds it to the in-memory exporter.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

PRESENT = "PRESENT"
ABSENT = "ABSENT"

EMPTY_COMPONENTS_REASON = "component list empty — inventory feed broken or unsigned"
MALFORMED_COMPONENTS_REASON = (
    "components file must parse to a non-empty JSON array of component names"
)
NO_BACKEND_REASON = "no trace backend bound; cannot measure, so not green"


class TraceBackend(Protocol):
    """The one question the gate asks of any trace backend."""

    def span_count(self, component: str, window_seconds: float) -> int:
        """How many spans this component emitted within the window."""
        ...


@dataclass(frozen=True)
class CoverageRow:
    component: str
    status: str
    span_count: int


@dataclass(frozen=True)
class CoverageReport:
    rows: tuple[CoverageRow, ...]
    window_seconds: float
    reason: str = ""

    @property
    def red(self) -> bool:
        return bool(self.reason) or any(row.status == ABSENT for row in self.rows)

    def as_dict(self) -> dict[str, object]:
        return {
            "gate": "otto-obs-coverage",
            "result": "red" if self.red else "green",
            "reason": self.reason,
            "window_seconds": self.window_seconds,
            "components": [
                {
                    "component": row.component,
                    "status": row.status,
                    "span_count": row.span_count,
                }
                for row in self.rows
            ],
        }


def check_coverage(
    components: Sequence[str],
    backend: TraceBackend,
    window_seconds: float = 900.0,
) -> CoverageReport:
    """PRESENT/ABSENT per component, straight from the backend's answer.

    An empty component list is RED, never green: a gate that checked
    nothing has proved nothing, and the empty list is exactly what a
    truncated or mis-generated inventory feed produces (the silent-green
    defect class). Non-name entries are the same malformed-feed red.
    """
    bad = [c for c in components if not isinstance(c, str) or not c.strip()]
    if not components or bad:
        reason = (
            EMPTY_COMPONENTS_REASON
            if not components
            else f"{MALFORMED_COMPONENTS_REASON}; bad entries: {bad!r}"
        )
        return CoverageReport(rows=(), window_seconds=window_seconds, reason=reason)
    rows = []
    for component in components:
        count = backend.span_count(component, window_seconds)
        rows.append(
            CoverageRow(
                component=component,
                status=PRESENT if count > 0 else ABSENT,
                span_count=count,
            )
        )
    return CoverageReport(rows=tuple(rows), window_seconds=window_seconds)


def main(
    argv: Sequence[str] | None = None,
    backend: TraceBackend | None = None,
    stdout=None,
) -> int:
    """Gate CLI: red (any ABSENT component) exits nonzero.

    ``backend`` binds at integration; running without one is itself a red
    — the gate never reports green when it cannot measure (no silent
    green).
    """
    parser = argparse.ArgumentParser(prog="otto-obs-coverage")
    parser.add_argument(
        "--components-file",
        required=True,
        help="JSON array of component names, generated from the signed "
        "capability inventory (never hand-kept)",
    )
    parser.add_argument("--window-seconds", type=float, default=900.0)
    args = parser.parse_args(argv)
    out = stdout if stdout is not None else sys.stdout

    with open(args.components_file, encoding="utf-8") as fh:
        components = json.load(fh)
    if not isinstance(components, list):
        print(
            json.dumps(
                {
                    "gate": "otto-obs-coverage",
                    "result": "red",
                    "reason": MALFORMED_COMPONENTS_REASON,
                    "parsed_type": type(components).__name__,
                }
            ),
            file=out,
        )
        return 2
    if backend is None:
        print(
            json.dumps(
                {
                    "gate": "otto-obs-coverage",
                    "result": "red",
                    "reason": NO_BACKEND_REASON,
                }
            ),
            file=out,
        )
        print(
            "otto-obs-coverage: no backend bound — see W2 wiring "
            "(SigNoz binds the TraceBackend Protocol at integration)",
            file=sys.stderr,
        )
        return 2
    report = check_coverage(components, backend, args.window_seconds)
    print(json.dumps(report.as_dict()), file=out)
    return 1 if report.red else 0


if __name__ == "__main__":
    raise SystemExit(main())
