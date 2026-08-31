# Onboarding: the Otto spine (CP1)

## What is this for

`crew#768` asks for a new Otto Agent Platform, built clean on a new branch
so the currently running Otto is never touched. CP1 is checkpoint one of
that build: a task envelope every future Otto message carries, a message
bus for it to travel on, a way to replay any task's full history from the
bus alone (no separate log store to trust), and a signed list of what
components actually shipped. Every later checkpoint (the tool gateway, the
memory engine, the eval harness's real corpus) is built on top of this,
not beside it.

## What does it cost

Nothing recurring against production: CP1 lives entirely under
`otto/spine/` and `otto/tests/cp1/`, new top-level directories, and is not
wired into any existing entry point yet — it does not run until a later
checkpoint decides how the `otto` binary is packaged and deployed.
Locally, its own test suite spins up an ephemeral `nats-server -js` and a
throwaway Postgres cluster and tears both down when the run ends; nothing
it does touches the estate's real NATS or Postgres.

## What does it watch or change

Nothing today — CP1 has no running deployment. What it defines: the
envelope format (`otto/spine/envelope.py`), the JetStream subject
taxonomy `otto.*.v1.>` (`otto/spine/subjects.py`), and the transactional
outbox pattern (`otto/spine/outbox.py`) that a later checkpoint's real
event flow will use for at-least-once delivery.

## Where it lives

`otto/spine/` (envelope, bus, outbox, replay, inventory, eval_runner,
cli) and `otto/tests/cp1/` (the Gherkin feature file, the pytest-bdd step
definitions, and the real-infrastructure fixtures in `conftest.py`), both
in this repo, branch `otto/cp1-spine`.

## How do I turn it off

There is nothing to turn off — it is not started by anything. Delete the
branch, or leave it unmerged; no other file in the repo imports from
`otto.spine` yet.

## How do I turn it back on

`git checkout otto/cp1-spine` and run
`python3 -m pytest otto/tests/cp1/step_defs/ -v` to see it work.

## What goes wrong

`otto inventory` refuses to run without `--verify-signature` — there is
no unsigned path, by design, so a caller cannot accidentally trust an
inventory nobody checked. The Ed25519 signing key is read from
`OTTO_INVENTORY_KEY_PATH` with a documented local fallback (LAW 46: never
a hardcoded path); a missing key means the command fails loudly rather
than inventing a fake pass. The test suite needs a `nats-server` binary
and a working `initdb`/`pg_ctl` on `PATH`; without them the fixtures fail
in `conftest.py` at setup, not partway through a scenario with a
misleading partial result.
