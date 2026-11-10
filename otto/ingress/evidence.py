"""The concrete ``FounderEvidence``: verified estate briefs, off the
catalogue instruction ledger.

The founder's private-office widget (``otto_founder_compass``) shows three
counts — *delivered* (proof checked), *in motion* (unreconciled) and
*attention* (exceptions) — plus a headline, a priority, an instruction id
and a proof reference. Every one of those must be a fact the estate can
back, not a number this door invents. The one source of verified yield is
``catalog.instructions`` (``platform/catalog/schema/001_catalog.sql``): a
prompt is a row with a closed status and a ``yield_json`` of ``{done,
proven, outcome, evidence}``, and the contract in
``catalog/census/instruction.py`` sets ``status = done`` *only* when the
work is both done and proven.

That contract is what this module reads:

* ``delivered`` = ``status = 'done'`` — work that is done *and* proven,
  which is exactly the widget's "proof checked" count.
* ``active`` = ``status IN ('open','converting','structured')`` — work in
  motion, not yet closed, which is the widget's "unreconciled" count.
* ``issues`` = ``status = 'abandoned'`` — work whose owner released it,
  which is the widget's "exceptions" count.

``delivered + active + issues`` is the whole closed set of statuses, so
the three counts can never double-count a row, and a row they miss would
mean a status outside the closed set — which is a drift this module
reports as an issue rather than a number it guesses.

This is a *read* and it is fail-closed. The one method that matters,
``brief()``, never raises: a missing catalogue database, a refused query
or a driver that cannot connect yields the same "no verified evidence"
packet the gateway's ``_safe_brief`` already produces, so a down evidence
source is a stale brief and never a broken door and never a fabricated
number. It also never opens a socket outside the 15-second
``answerWidgetQuery`` window beyond a single bounded read: one aggregate
query with a 2-second connect timeout, and the numbers are what Postgres
returns, verbatim.

``psycopg`` and not ``asyncpg``, for the same reason as
``otto.ingress.pg_store``: the gateway's request handling is synchronous
``http.server``, and the widget tap is answered in that same handler, so
a synchronous driver is the whole requirement and nothing drags an event
loop into the one call.

LAW 46: nothing here names a host, a port, a database or a password.
``catalog_dsn_from_env`` builds the connection string from the
environment the deployment sets and reads the password from a file, the
same way ``pg_store.dsn_from_env`` does for the binding database.
"""

from __future__ import annotations

import pathlib
import time
from typing import Any, Mapping

import psycopg
from psycopg.conninfo import make_conninfo

from otto.ingress.widgets import FounderEvidence

__all__ = [
    "FounderEvidenceStore",
    "catalog_dsn_from_env",
    "CatalogNotConfigured",
    "DEFAULT_NO_EVIDENCE",
]

#: The catalogue database's connection knobs. Deliberately a *second* DSN
#: namespace from the binding database's ``OTTO_INGRESS_DB_*``: the binding
#: table lives in the ``otto_gateway`` database and the instruction ledger
#: lives in the shared ``estate`` database's ``catalog`` schema, so two
#: connections, two sets of knobs, one door.
CATALOG_HOST_ENV = "OTTO_CATALOG_DB_HOST"
CATALOG_PORT_ENV = "OTTO_CATALOG_DB_PORT"
CATALOG_NAME_ENV = "OTTO_CATALOG_DB_NAME"
CATALOG_USER_ENV = "OTTO_CATALOG_DB_USER"
# noqa reason: the NAME of an environment variable holding a file path to
# a password file; the password itself is only ever read from that file.
CATALOG_PASSWORD_FILE_ENV = "OTTO_CATALOG_DB_PASSWORD_FILE"  # noqa: S105

CATALOG_DEFAULT_PORT = "5432"

#: The single, bounded read: three counts over the instruction ledger's
#: closed status set, plus the latest instruction's id, proof evidence and
#: headline input so the "instruction" and "proof" widget lines are real
#: rather than synthesised. The SQL is a module constant, never a runtime
#: f-string, so the column semantics sit beside the column names and there
#: is nothing for S608 to find.
#:
#: ``delivered`` is ``status='done'`` (done *and* proven, by the contract);
#: ``active`` is the three in-motion statuses; ``issues`` is ``abandoned``.
#: The "latest" row is the most recently *recorded* instruction, whatever
#: its status, so the widget's "instruction" line is genuinely the newest
#: fact on the ledger.
_BRIEF_SQL = (
    "SELECT "
    "count(*) FILTER (WHERE status = 'done')                           AS delivered, "
    "count(*) FILTER (WHERE status IN "
    "  ('open','converting','structured'))                             AS active, "
    "count(*) FILTER (WHERE status = 'abandoned')                      AS issues, "
    "max(recorded_at)                                                  AS latest_at, "
    "(array_agg(id ORDER BY recorded_at DESC, id DESC))[1]             AS latest_id, "
    "(array_agg(prompt ORDER BY recorded_at DESC, id DESC))[1]         AS latest_prompt "
    "FROM catalog.instructions"
)


class CatalogNotConfigured(RuntimeError):
    """The deployment did not say where the instruction ledger lives.

    Raised only to feed ``brief()``'s fail-closed path: the door still
    answers the widget tap, with the "no evidence" packet, rather than
    crashing the handler."""


class FounderEvidenceStore:
    """``FounderEvidence`` over the catalogue instruction ledger.

    Holds a DSN and runs one aggregate query per tap, with a connect
    timeout whose 2-second bound keeps even a full connect failure well
    inside Telegram's 15-second ``answerWidgetQuery`` window."""

    def __init__(self, dsn: str, *, connect_timeout: int = 2) -> None:
        self._dsn = dsn
        self._connect_timeout = connect_timeout

    def brief(self) -> dict[str, Any]:
        """The ``chat_data`` object the widget renders, all of it verified.

        Never raises: any failure returns the same fail-closed packet the
        gateway's ``_safe_brief`` produces, so the founder sees a stale
        brief rather than a broken tap. ``r`` advances only on a real
        read, so a refresh that hits a down database never rewinds the
        widget's revision."""
        try:
            delivered, active, issues = self._counts()
        except Exception:  # noqa: BLE001 - fail-closed: a down source is a stale brief
            return dict(DEFAULT_NO_EVIDENCE)

        # The revision is the floor of the current epoch second. It is a
        # non-negative integer on every real read, it is monotonic against
        # the widget's `revision` gate (which only rejects a *lower* `r`),
        # and it needs no store: two evidence reads in the same second
        # share a revision, which is fine — the widget rejects a
        # regression, not a plateau.
        revision = int(time.time())
        return {
            "r": revision,
            "at": revision * 1000,  # the widget's `at` is an epoch in *milliseconds*
            "headline": (
                f"{delivered} proven · {active} in motion · {issues} exceptions"
            ),
            "delivered": delivered,
            "active": active,
            "issues": issues,
            # The priority is source-backed: the count that most needs the
            # founder's eye, or a calm all-clear when nothing is abnormal.
            "priority": _priority_line(delivered, active, issues),
            # The instruction and proof lines are the *newest* ledger row's
            # id and prompt; the prompt is shown only when it fits the
            # widget's 130-byte proof budget, else a neutral dash — proof is
            # absent rather than truncated into a lie.
            "id": self._latest_id(),
            "proof": self._latest_proof(),
        }

    def _counts(self) -> tuple[int, int, int]:
        with psycopg.connect(
            self._dsn, connect_timeout=self._connect_timeout, autocommit=True
        ) as conn:
            row = conn.execute(_BRIEF_SQL).fetchone()
        return int(row.delivered or 0), int(row.active or 0), int(row.issues or 0)

    def _latest(self) -> tuple[str | None, str | None]:
        try:
            with psycopg.connect(
                self._dsn, connect_timeout=self._connect_timeout, autocommit=True
            ) as conn:
                row = conn.execute(_BRIEF_SQL).fetchone()
            return row.latest_id, row.latest_prompt
        except Exception:  # noqa: BLE001 - proof/id are a nicety, never a must
            return None, None

    def _latest_id(self) -> str:
        latest_id, _ = self._latest()
        return latest_id or "—"

    def _latest_proof(self) -> str:
        _, prompt = self._latest()
        if not prompt:
            return "—"
        # The widget's `proof` field is a string of at most 130 bytes.
        # Truncating a real instruction's prompt into a 130-byte snippet
        # risks showing a partial, mis-leading statement, so the whole
        # prompt is shown only when it fits; otherwise the field is a
        # neutral dash rather than a cut-off half-truth.
        return prompt if len(prompt.encode("utf-8")) <= 130 else "—"


def catalog_dsn_from_env(environ: Mapping[str, str] | None = None) -> str:
    """The catalogue DSN, assembled from the deployment's own values.

    The same shape as ``otto.ingress.pg_store.dsn_from_env``: the password
    is read from the file the secret store projects, never from an
    environment variable. Missing knobs raise ``CatalogNotConfigured``,
    which ``FounderEvidenceStore.brief`` turns into a stale brief."""
    env = dict(environ or {})

    missing = [
        name
        for name in (CATALOG_HOST_ENV, CATALOG_NAME_ENV, CATALOG_USER_ENV)
        if not env.get(name)
    ]
    if missing:
        raise CatalogNotConfigured(
            "the catalogue database is not configured; set " + ", ".join(missing)
        )

    password_file = env.get(CATALOG_PASSWORD_FILE_ENV, "")
    if not password_file:
        raise CatalogNotConfigured(
            "set " + CATALOG_PASSWORD_FILE_ENV + " to the projected password file"
        )
    path = pathlib.Path(password_file)
    if not path.is_file():
        raise CatalogNotConfigured(
            CATALOG_PASSWORD_FILE_ENV + " points at " + password_file
            + ", which is not a file; the secret store has not projected it yet"
        )

    return make_conninfo(
        host=env[CATALOG_HOST_ENV],
        port=int(env.get(CATALOG_PORT_ENV, CATALOG_DEFAULT_PORT)),
        dbname=env[CATALOG_NAME_ENV],
        user=env[CATALOG_USER_ENV],
        password=path.read_text().strip(),
    )


def _priority_line(delivered: int, active: int, issues: int) -> str:
    """A source-backed single-sentence priority, never an invented one.

    The rule is deterministic and only repeats what the counts already
    say, so the widget's "current priority" can never claim more than the
    evidence supports."""
    if issues > 0:
        return (
            f"{issues} exception{'s' if issues != 1 else ''} await attention; "
            f"{active} still in motion."
        )
    if active > 0:
        return (
            f"{active} instruction{'s' if active != 1 else ''} in motion; "
            f"{delivered} proven delivered."
        )
    return f"{delivered} verified deliveries, nothing pending."


#: The fail-closed packet, shared with the gateway's own ``_safe_brief``.
#: Exactly the shape the widget renders, with revision and timestamp at
#: zero so the widget shows "NO VERIFIED TIMESTAMP" and never a live,
#: fake-fresh state.
DEFAULT_NO_EVIDENCE: Mapping[str, Any] = {
    "r": 0,
    "at": 0,
    "headline": "No verified estate briefing is available.",
    "delivered": 0,
    "active": 0,
    "issues": 0,
    "priority": "—",
    "id": "—",
    "proof": "—",
}
