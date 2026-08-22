# Decisions

Rulings that outlive the pull request that caused them. One entry per decision,
newest last. A decision here is binding until an entry below it says otherwise.

---

## 2026-08-22 — Every service gets its own verify harness

**The ruling, in the founder's words:** every service gets its own verify
harness, a PASS/FAIL count, and no prose.

A service is done when a command says so. Not when an agent says so, not when a
pull request body says the tests pass, and not when a document describes the
behaviour accurately. Prose drifts from the machine and nothing notices; a
count cannot.

**What a harness has to be.** One command. A row per check, each row a real
command and its real output. A `PASS n, FAIL n, SKIP n` line at the end. The
exit code is the verdict, and it is non-zero when `FAIL` is non-zero.

**SKIP is a result, not a failure.** These harnesses run on the founder's
laptop, on a VPS, and in CI, and those machines do not have the same things on
them. A row that cannot apply says so and is counted separately. A harness that
fails because it is not on a Mac gets ignored within a week, and an ignored
harness is worse than none. The exception is security: a world-readable
credential is a FAIL wherever it is found.

**A number that measures two things is a lie.** The first consult harness timed
one round-trip and failed at 32s. The 32s was a local model being read off
disk; the same call warm took 2s. One row would have been loosened until it
could never fail. It is now two rows, cold and warm, with different ceilings,
because those are two different faults.

**The harness runs on every pull request** and its output is the LAW 22
evidence image. A pull request whose harness reports any `FAIL` is not ready
for review.

**First instance:** `bin/verify-consult`, §16. Reachable estate-wide as
`~/.claude/scripts/consult-verify.sh`.

---

## 2026-08-22 — No Gemini in the consult cascade

Removed on the founder's instruction. It had earned it independently: the free
tier was spent, and its CLI retries a rate-limit internally rather than
returning one, so it spent its entire time cap on every consult and then failed
anyway.

**The general rule this leaves behind:** a backend that cannot fail fast is
worse than no backend, because a fallback chain is only as useful as its
slowest refusal. Anything added to the cascade carries a time cap and is
removed if it cannot honour it. Do not add Gemini back without a measurement
showing it answers.

---

## 2026-08-22 — All repos live at ~/dev/code/. One root, no others.

**The ruling, in the founder's words:** all repos at `~/dev/code/`. No more
`~/Documents/` or `~/code/` roots.

Three checkouts of hermes-v2 existed at once today: `~/Documents/code/hermes-v2`,
`~/dev/code/hermes-v2`, and briefly `~/code/hermes-v2`. One session archived the
Documents copy to `hermes-v2.ARCHIVED.20260822` while another was still working
in it. Nothing was lost, because the work was already pushed, but that was luck
and not design: the recovery depended on `git rev-list --count` returning 0.

Sessions cannot see each other. Two copies of one repo is two answers to "what
is the current state", and the second copy is discovered by tripping over it.

**What this means in practice.** Clone into `~/dev/code/<repo>`. Before starting
work, confirm you are in it. A checkout found anywhere else is stale by
definition: push anything unique, then retire it. Never create a new root
because the one you wanted was missing.

`~/Documents/code/hermes-v2.ARCHIVED.20260822` holds no unique commits and is
retired.
