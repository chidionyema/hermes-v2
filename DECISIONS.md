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

## Kimi reaches the estate through a browser bridge, with ollama underneath

2026-08-22. Kimi For Coding is a separate subscription from the Kimi web app,
and it cannot be bought right now: the account is on a waiting list. Measured on
the same working OAuth token, `GET api.kimi.com/coding/v1/me` returns 200 while
`/models` and `/chat/completions` return 402. Every other paid provider on this
machine is out of credit as well: DeepSeek 402, MiniMax status 2056, OpenRouter
10 of 10 credits spent. The free local floor cannot be raised either, because a
14B model is 9GB against 11GB of free disk.

So the bridge drives the signed-in web session instead. It is the primary
consult backend and ollama sits under it.

**What it is.** `~/.claude/scripts/kimi_bridge.py`, a daemon on 127.0.0.1:8766
speaking `/query` and `/health`. A dedicated Chromium profile at
`~/.kimi-bridge/profile`, never the founder's own browser. Session state in
SQLite at `~/.kimi-bridge/session.db`, 0600. Health probed every 60 seconds with
an automatic restart when it goes stale, and three retries at 2, 4 and 8 seconds.
Fingerprint patches so the page sees a hand-driven browser. Logs redact anything
shaped like a credential before it is written.

**Two ways to read an answer, on purpose.** The network capture reads the
response the page itself received, so it survives a redesign. The DOM settle
watcher is the fallback and only survives until someone renames a class. The
bridge reports which one answered. A single detector that silently stops
matching returns an empty string, which is worse than an honest failure.

**Two interpreters, one port.** The bridge needs Playwright and runs in its own
virtualenv. `consultd.py` runs under Apple's signed `/usr/bin/python3` so macOS
does not re-prompt on every launch. They share nothing but loopback HTTP, and
`consultd.py` imports the shim inside a `try`, so a broken Playwright install
starts the cascade one backend shorter instead of stopping the daemon.

**Known and accepted.** This is against Moonshot's terms and the account it
risks is the one in daily use. The founder was told twice and decided twice; it
is logged here so the next agent inherits the trade rather than relitigating it.
A UI change will break it, it will retry, and then ollama answers.
