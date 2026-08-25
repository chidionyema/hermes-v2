# Onboarding: verify-on-stop

## What is this for

The claim gate labels a DONE: that the verification ledger cannot back. This
switch is the other half: it nudges the agent, when it has edited files and
is about to finish, to run the project's verification command first — so its
DONE: claims come with a green run behind them instead of being stamped
UNVERIFIED. It was OFF on Telegram by upstream default; your only surface is
Telegram, so OFF meant the ledger never gained a single proof run.

## What does it cost

One extra command run (the project's test or verify command) at the end of
turns where the agent edited code. No new process, no new job. Slightly
longer turns on edits, in exchange for claims you can trust.

## What does it watch or change

`agent.verify_on_stop: true` in `config.yaml`. The setting is read fresh on
every check, so it took effect without a restart. It changes agent behavior
only — the ledger and gate machinery were already in place.

## Where it lives

`config.yaml` line ~12, read by
`hermes-agent/agent/verification_stop.py::verify_on_stop_enabled`.

## How do I turn it off

Ask any session to set `agent.verify_on_stop: false` in
`~/dev/code/hermes-v2/config.yaml` — it applies on the next turn, no restart.

## What goes wrong

In a repo with no recognized test command the nudge has nothing to run and
stands down. As of 2026-08-24 hermes-v2, crew, maestro and ~/.claude/scripts
all resolve a verify command (scripts/run_tests.sh or pytest); the one root
without one is ~/.claude itself, because its only candidate check
(estate-selftest.py) was failing on the day and wiring a red suite as the
gate would fake protection (crew #63 A1). If turns on Telegram start feeling slow or noisy
after edits, that is this switch, and one line turns it off.
