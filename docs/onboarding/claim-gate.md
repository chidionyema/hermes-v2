# Onboarding: the claim gate

## What is this for

You asked for a way to stop agents making statements that are false. This is
the first mechanical piece: when an agent's Telegram reply opens with
`DONE:` but the session's verification ledger shows it edited files and never
ran a green verification afterwards, the word is rewritten `UNVERIFIED:` and
one footer line says which workspace and why. You still get the agent's full
reply — you just also get an honest label it cannot forget to add.

## What does it cost

Nothing recurring: one local SQLite read per reply that claims DONE. No new
process, no new job, no network calls.

## What does it watch or change

It reads verification_evidence.db (written automatically when agents edit
files and run tests) and changes only the first status word of a reply plus
one appended line. Replies saying WORKING or BLOCKED, doc-only work,
conversation, and anything already proven green pass through untouched. It
never blocks, delays, or shortens a message.

## Where it lives

`hermes-agent/gateway/claim_gate.py`, wired into the reply path in
`hermes-agent/gateway/run.py`, shipped as `patches/hermes-agent/0003`. The
status-line convention it reads is mandated in `SOUL.md`.

## How do I turn it off

Ask any session to set `HERMES_CLAIM_GATE_DISABLED=1` in the gateway's
environment and restart the gateway. Every stamp it applies is logged, so
how often it fires is countable.

## How do I turn it back on

Remove that variable and restart the gateway.

## What goes wrong

If the ledger is empty (a repo with no verify markers, or a session id the
ledger never saw), the gate passes everything — it fails toward trusting the
agent, never toward breaking a reply. That means an unmarked repo gets no
protection yet; marking the estate's repos is the follow-up (crew #63 A1).
A stamp on work you know was fine means the agent edited files and skipped
the proof run — which is exactly what the label is telling you.
