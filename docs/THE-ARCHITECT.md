# THE ARCHITECT — master build spec
### Autonomous engineering estate on Hermes · GitHub · your phone

Supersedes `phone-run-estate.md` and `hermes-day0-config.md` (both folded in).
Every factual claim is graded in §14: **[V]** verified against primary source,
**[R]** reported by credible secondary source, **[D]** my design decision.

---

<a id="s0"></a>

## 0. Principles (each one earned, not aesthetic)

1. **The orchestrator is not an agent.** GitHub Projects is the queue, labels
   are the state machine, PRs are the output, and **green CI is the promotion
   gate**. An LLM polling a queue is Opus tokens spent on `ORDER BY`. [D]
2. **Two minds, one board, no shared memory.** Cron sessions pass
   `skip_memory=True` and memory providers intentionally do not run there
   (`hermes-agent/AGENTS.md:1047`), so a cron lane writes nothing to memory at
   all — and two writers on one memory compound into state nobody authored.
   All inter-agent state flows through GitHub issues/comments. [V]
3. **Verification by execution, never by introspection.** A claim exists only
   with a command and its output attached. Self-checking techniques are the
   weak form; CI is the strong form. [D, grounded in AIDE² results]
4. **Selection beats instruction.** Honesty, efficiency, and non-recurrence
   are properties you get from what survives the gates, not from prompt
   adjectives. The agent optimises whatever the gates admit, so the gates —
   not the prose — are the specification. [V — Weco]
5. **Lessons compile downward.** A lesson in prose is probabilistic; a lesson
   as a test is deterministic. Every incident pushes its lesson as far down
   the ladder as it admits (§8). [D, grounded in experience-following research]
6. **Less context per role, more attempts.** The winning evolved agent cut
   context 16× per operator and reinvested tokens as search steps. Seed
   memory tersely; give each profile only what its role needs. [V — Weco]
7. **The scorer is not the scored.** With the founder tap gone, CI is the
   whole objective function — so no lane may edit what grades it. Gate
   definitions, branch protection and the eval corpus are outside every
   agent's write scope, and the production oracle (§7c) is a signal the
   agent never sees before merge. An agent that can rewrite its own test
   always passes it, which removes the oracle rather than satisfying it. [D]
8. **Heal first, speak on exception.** A monitor that reports a condition it
   could have fixed has chosen narration over work. Known signature → remedy →
   verify → stay silent. Unknown, or the remedy failed twice → escalate, once,
   with the evidence. Everything else is a weekly line nobody has to read. [D]
9. **A lesson is admitted by measurement, not by opinion.** Skills accumulate
   automatically, and the thing that admits or deletes an entry is a replay
   over the incident corpus, not a review. A lesson that does not improve
   the batch is deleted on the run that proves it. [V — MNL]

---

<a id="s1"></a>

## 1. Topology

```
  your phone
  ├── Telegram ───────► Hermes gateway  (chat, screenshots, ad-hoc, approvals)
  └── GitHub mobile ──► issues / PRs / project board  (control plane)

  VPS  (£5–15/mo; the laptop becomes optional)
  ├── profile WATCH   read-only creds · cheap model lane · cron-driven
  └── profile WORK    worktree+PR only · frontier lane · label-triggered
        └── may drive Claude Code / other harnesses as sub-tools (bundled skill)

  platform (Fly.io — prospector-* apps, lhr)
        ◄── read-only telemetry ── WATCH
        ◄── merged PRs only ────── CI  (all required checks green = merge)
        ──► post-merge verdict ──► production oracle (§7c) — rollback on red
```

States on the board: `triage → ready → in-progress → pr-open → merged →
deployed → verified → closed`, plus `proposal`, `post-mortem` and `held`.
Labels are applied by WATCH (opening, and `ready` when the issue carries a
falsifiable acceptance command), WORK (the rest), and CI (`merged` onward).
No label is yours to apply for the loop to run.

Two labels stop it, and they are the founder's: `held` freezes an issue where
it stands, and `no-auto` on a path in `CODEOWNERS` forces review on anything
touching it. The tap moved from *every* change to *the changes you name in
advance* — which is the same authority exercised once instead of hourly.

---

<a id="s2"></a>

## 2. Day-0 install (hour 1)

```bash
# VPS, not laptop
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes setup && hermes doctor        # proceed only when clean
# create the two profiles = two separate Hermes homes (never shared)
```

Pin explicitly (defaults have shifted across releases):

```yaml
agent:
  max_turns: 90
memory:
  memory_enabled: true
  user_profile_enabled: true
```

Verify smart approvals are ON. Pair Telegram. Model needs ≥64K context.
Lanes: WATCH on a cheap model; WORK on frontier; auxiliary tasks (compression,
session search, memory jobs) pinned to a cheap reliable route with a
fallback chain — auxiliary provider failures kill turns silently otherwise.

---

<a id="s3"></a>

## 3. Memory (hour 2)

**Layer 1 — built-in files, seeded by hand, tersely.**
- `MEMORY.md` (both profiles): platform map — Fly apps and regions, service
  dependencies, where logs live, healthy baselines, deploy path, repo map,
  runbook index. Memory is character-capped and errors on overflow rather
  than silently dropping; terse survives, prose doesn't.
- `USER.md`: timezone, tone, escalation rules, and the line that implements
  the 1am-mumble handler: *"On ambiguous instructions, restate your
  interpretation in one line and wait for confirmation before acting."*

**Layer 2 — session archive (automatic).** SQLite + FTS5; the agent recalls
weeks-old sessions via `session_search`. Ops: nightly off-box backup of
`state.db`; treat it as a secrets liability — prod credentials live in
tool-scoped env, never in the conversation path.

**Layer 3 — one external provider (week 2, not day 0).** Single slot beside
the built-ins. Shortlist: Hindsight (retains full turns incl. tool calls —
the RCA choice), Mem0 (zero-maintenance extraction), Holographic (fully
local). Start without; add when session_search stops being enough.

**Rules.** One agent process per Hermes home, always. Weekly `/journey`
pruning from the phone (~10 min): "never forgets" is native, "remembers
correctly" is this ritual — and per §8, deletion of bad memories is a
first-class safety operation, not housekeeping.

---

<a id="s4"></a>

## 4. Skills (hour 3 + day 1–2)

**Prune per profile.** WORK keeps: `github-pr-workflow`, `plan`,
`test-driven-development`, debugging/sysadmin set, `arxiv`, and the
`claude-code` skill (Hermes drives Claude Code as a sub-harness — your
multi-harness requirement met by composition). WATCH goes near blank-slate
(`hermes skills opt-out`; safe, never deletes user skills) plus only what
perception needs.

**Gate per platform.** `hermes skills` TUI → dev/deploy skills OFF on the
Telegram surface. The phone talks about everything, invokes little.

**The six estate skills (you write these; nobody can do it for you):**

1. `estate-map` — services, deps, thresholds, runbook links. Referenced by all.
2. `incident-triage` — investigation order here: which logs first, correlation
   sequence, known failure signatures, when to stop and ask.
3. `pr-discipline` — worktree, reproduce-first, tests-first, Evidence block,
   linked issue, plain-English summary.
4. `screenshot-to-story` — your user-story format; output = labelled issue.
5. `verify-to-prod` — "shipped" means merged → deployed → health green →
   issue closed with post-deploy evidence.
6. `post-mortem` — the never-twice engine (§8): lesson, ladder rung, eval
   case, regression artifact.

Dispatch by chaining (instincts without being told):
`/github-pr-workflow /test-driven-development /pr-discipline fix issue #N`.

Skills follow the agentskills.io open standard — the same SKILL.md loads in
Claude Code and other compatible runtimes. One corpus, every harness.

**Provenance (hour 4, closes Hermes's audit gap):**

```bash
cd ~/.hermes/skills && git init && git add -A && git commit -m "day 0"
# hourly cron:
git add -A && git diff --cached --quiet || \
  git commit -m "skill drift $(date -Iseconds)" && git push
```

Every self-modification — agent-created skill, patch, Curator archive —
becomes a diff readable from your phone.

---

<a id="s4b"></a>

## 4b. Ecosystem loadout — community skills & plugins (researched)

Sources: bundled catalog (~70–94 skills), official optional catalog, Skills Hub
(~650 across registries), and the awesome-hermes-agent community index (4k+
stars, reviewed 2026-05). Rule: skills are context tax (every install adds to
the always-loaded index) and supply chain (instructions + scripts your agent
runs). Curate hard; vet everything.

**WORK — install day 0**

| item | kind | why | grade |
|---|---|---|---|
| `oh-my-hermes` (install `ralplan`, `ralph`, `triage`, `deep-research`, `deep-interview`; **skip `autopilot`**) | skill suite | ralph = verified execute→verify→iterate; ralplan = Planner→Architect→Critic consensus — your "asks right questions" + verification loop as procedure | beta |
| `execplan-skill` | skill | checkpoints, progress, failure recovery for long tasks — patches Hermes's no-checkpoint gap | beta |
| `hermes-agent-acp-skill` | skill | routes subtasks across Hermes / Claude Code / Codex — multi-harness by one install | beta |
| `rtk-hermes` | plugin | compresses shell output 60–90% pre-context; the Weco less-context finding as software | beta |
| `hermes-web-search-plus` | plugin | multi-provider search routing (Serper/Tavily/Exa) — the Otto search stack, pre-built | beta |
| `agenttrace` + its audit skill | tool+skill | post-run session audits: cost spikes, tool failures, retry loops, anomalies — fills leash item 3 | beta |
| `lintlang` | CI-side tool | static linting of agent configs/prompts — a §7 gate for the agent's own config | beta |
| `drawio-skill` | skill | NL → architecture diagrams; useful for the docs-drift attack | production |
| bundled: `github-pr-workflow`, `plan`, `test-driven-development`, `claude-code`, debugging/sysadmin set | bundled | already in §4 | builtin |

**WATCH — install day 0**

| item | why | grade |
|---|---|---|
| `rtk-hermes` | log-heavy profile = biggest token win on the list | beta |
| `hermes-ai-infrastructure-monitoring-toolkit` | monitoring + cost forecasting + Telegram alerting on cron | beta |
| `hermes-incident-commander` — **reference only, do not install on any write-capable profile** | its detection logic feeds your `incident-triage` skill; its "applies fixes autonomously" half violates read-only WATCH and the promotion gate | beta |

**Month 2, deliberately (one new self-mod channel at a time)**

`hermes-curator-evolver` (evidence-driven Curator companion: dry-runs,
backups, rollback manifests, low-risk-only autorun — best philosophical fit
with §8 admission control) OR `SkillClaw` (production-tagged auto-evolution
from session data with doctor/restore) — not both, not day 0: they'd be a
third self-modification pathway beside Curator + GEPA. Also month 2+:
alternate memory providers (Mnemosyne, yantrikdb — the latter surfaces
contradictions instead of overwriting, aligned with §8), an operator
cockpit (hermes-workspace GUI or mission-control fleet dashboard — optional;
GitHub stays the source of truth), cherry-picks from the 753-skill
MITRE-mapped security pack, `camofox-browser` if VPS browsing gets blocked.

**Skip list (v1, on purpose)**

Payment autonomy (AgentCash wallet, payguard) · anything autopilot-shaped ·
entertainment/media/blockchain/social packs · experimental-tagged items near
prod · bulk-installing any "broad library" — cherry-pick or nothing.

**Vetting ritual (every community install, no exceptions)**

1. Read the SKILL.md *and every file in scripts/* before install — you are
   reviewing instructions your agent will follow and code it will run.
2. Prefer production-tagged and official; beta only with a read-through;
   experimental never touches a prod-adjacent profile.
3. Install → immediate commit in the skills git repo (pin the version you
   reviewed; drift shows as a diff).
4. Per-platform gate it (§4) — new skills stay OFF the Telegram surface
   unless needed there.
5. Cap the index: ~15 installed skills on WORK, ~8 on WATCH beyond the
   estate six. At cap, an install must name the skill it replaces.

Ecosystem discovery when you need more: `hermes skills browse`, the Hub,
skilldock.io, hermeshub — same ritual every time.

---

<a id="s5"></a>

## 5. WATCH — perception (day 1)

Read-only credentials; verify by attempting a write and confirming failure.
Cheap model lane. It perceives and it selects a remedy; it never invents one.

Concrete wiring for your platform (adjust app names):

| cadence | job | mechanics |
|---|---|---|
| 15 min | pulse | `fly status -a prospector-engine`, `fly machine list`, health endpoints, error-rate window from `fly logs --no-tail` |
| on threshold | **heal** | match the signature against the remedy catalogue → run that remedy → re-pulse → **say nothing on success** |
| no match, or healed twice and back | investigate | correlate log window ± recent deploys (`gh run list`, `git log`) ± past incidents (session_search + closed issues) → open issue labelled `triage`, evidence inline |
| 07:00 | digest | **suppressed unless an exception is open.** A quiet estate sends nothing |
| Sun | review | read the heal ledger; anything healed 3+ times becomes a `triage` issue — a recurring heal is a defect, not a fix |
| Sun | proposals | 3 unasked-for improvements as `proposal` issues, each with the measurement that would prove it worked |

**The remedy catalogue** is the whole of what auto-healing may do. Each entry
is a signature, a script, a verify command and a stated blast radius — checked
into git, so adding a remedy is a PR under §7 and not a runtime decision:

```yaml
- signature: "engine 5xx > 2% for 2 consecutive pulses"
  remedy:  scripts/heal/restart-machine.sh --app prospector-engine
  verify:  bin/pulse.sh --app prospector-engine --require 2-consecutive-green
  radius:  one machine, one app, ~15s of 502s
  cap:     2 per 24h per app
```

The lane holds write credentials for **nothing but the catalogue's scripts**.
An LLM with production write access and a free hand is a larger blast radius
than the alert flood it replaces; selecting from a fixed list is not.

Three rules keep silence honest, and all three are the difference between
auto-healing and hiding:

- **Cap and flap.** Past `cap`, healing stops and the condition escalates. A
  remedy that keeps working on the same signature is masking a defect, and the
  cap is what converts it back into a visible one.
- **Every heal is written down even though nobody is told.** `logs/heal.jsonl`
  — signature, remedy, verify output, duration. Silence is the absence of a
  *message*, never the absence of a *record*.
- **The ledger is read on a schedule** (Sunday, above) and its recurring
  entries are promoted up the §8 ladder. A heal ledger nobody reads is how a
  system converts a fixable defect into a permanent operating cost.

Issue template (enforced by the skill): claim → query run → raw output →
time window → similar past incidents. No evidence, no issue. Thresholds and
the catalogue live in `estate-map`, so tuning them is a skill edit, not code.

Untrusted-input note: logs are attacker-influenceable text. WATCH's egress
allowlist (§10) is the control — constrain what it can reach, don't try to
make it un-foolable.

---

<a id="s6"></a>

## 6. WORK — hands (week 3, after the leash)

Trigger: the `ready` label, applied by WATCH when an issue carries a
falsifiable acceptance command — no human in the path. Frontier lane. Repo
write via worktree only; no push to main, no deploy rights, no prod DB, no
prod secrets in context, and **no write access to anything that grades it**
(§7b).

Loop: read issue + all comments (its briefing — no memory handoff) →
one-line interpretation posted; low confidence = label `held` and stop →
worktree → **reproduce first** (cannot reproduce = comment and stop; never
fix an unseen bug) → fix → verify by execution → PR with Evidence block →
stop. It still never merges — **CI does**, when every required check is
green and the branch touches no `no-auto` path.

Follow-through (`verify-to-prod`): after the merge and deploy, WORK
re-checks health + the incident's regression test in prod context, posts
post-deploy evidence, moves issue to `verified`, then `closed`. "Tracks to
shipped" is a state transition it owns, not a vibe. The Telegram ping moved
here from PR-open: you hear when something **reached production and passed**,
not when a machine wants permission.

---

<a id="s6b"></a>

## 6b. BENCH — local execution (optional; the laptop as ephemeral fleet)

The estate's exception path, not its spine. BENCH exists for work that
genuinely requires the laptop: your local dev env and logged-in sessions,
local-only services, un-pushed state, hardware, M-series-heavy builds.
Everything else stays on the VPS so the phone goal survives.

**Lifecycle — on-demand, never always-on.** No cron, no daemon, no gateway.
You open the laptop and start it (one command); it drains `needs-local`
issues from the board; idle → it exits. If it never runs, those issues
simply wait — the estate degrades gracefully to VPS-only by construction.

**Profile.** Own Hermes home (one agent per home, always). Minimal memory:
seed MEMORY.md with the dev-env map only; no external provider. Skills: the
estate six + `hermes-agent-acp-skill` + `execplan-skill` + `rtk-hermes`.
Telegram surface OFF for this profile — you're at the desk when it runs;
approvals are local; smaller attack surface.

**Fleet mechanics — concurrency without multiplicity.**
- Parallelism via subagents in worktree mode (`-w`), hard cap 3 concurrent —
  parallel hands, one dispatching mind, no persistent roster. (Bot Mode's
  named local agents exist as a desktop plugin; skip for v1 — persistent
  local minds are the fleet anti-pattern with extra steps.)
- Cross-harness dispatch: route subtasks to Claude Code / Codex via the
  bundled `claude-code` skill and acp routing where they're stronger; the
  "fleet" is mostly harnesses you already own, now coordinated.
- Interactive sessions in tmux (per docs guidance); observe via the TUI
  subagent overlay; optional cockpit: mission-control or hermes-workspace —
  read-only views, never a second queue.

**Coordination — three bans, absolute.**
1. No second board. `needs-local` and `bench-in-progress` labels on the one
   GitHub board are the entire protocol.
2. No cross-profile memory writes, ever — state moves as issue/PR comments.
3. No always-on. BENCH holding work hostage to the laptop being open is the
   architecture failing silently.

**Liveness through the board.** BENCH heartbeats by commenting on its
in-progress issue hourly. WATCH (which never sleeps) flags `bench-stale`
when a heartbeat lapses — lid-close mid-task becomes a visible label on
your phone, not a mystery. Worktrees survive the interruption; work resumes
or gets relabelled back to the queue.

**Reporting and RSI.** Same contract as WORK: PR + Evidence block, never a
merge. BENCH sessions land in its sessiondb — local trajectories are eval
data too.

---

<a id="s7"></a>

## 7. Gates (week 1, before WORK exists)

**Green CI is the promotion gate.** Auto-merge is on: every required check
green, `no-auto` paths untouched, no `held` label ⇒ the PR merges itself and
deploys. Nobody taps anything. Which makes the gates the entire specification
of what this estate will accept, so the rest of this section is about keeping
them out of the hands of the thing they grade.

**7a. Evidence gate — CI, mechanical, non-negotiable:** every PR body needs
`## Evidence` with one `### Claim:` per claim, each followed by the command
and real output. The workflow from the earlier sheet stands (counts claims
vs output blocks; fails on mismatch).

**7b. The scorer is out of reach.** The old asymmetry was *CI is the score the
agent can see, your merge is the score it can't*. The tap is gone, so the
asymmetry has to be rebuilt somewhere the agent cannot reach:

| what | where it lives | why it is out of reach |
|---|---|---|
| gate definitions (`.github/workflows/`, `ci/`) | `CODEOWNERS`-protected, `no-auto` | a PR touching them cannot auto-merge, ever |
| branch protection & required-check list | GitHub org settings | no API token in any lane can edit it |
| eval corpus (`estate-evals/incidents.jsonl`) | append-only, enforced in CI | the agent adds cases; it cannot alter or delete one |
| holdout slice | 20% of the corpus, never in the agent's context | it is scored against cases it has not read |
| production oracle (§7c) | post-merge | the signal does not exist at PR time |

A gate the agent can edit is not a gate, and a test that rewrites itself to
match the code always passes — which removes the oracle rather than satisfying
it. Those five rows are the difference between selection pressure and a
closed loop congratulating itself.

**7c. Production is the score it never sees.** Merge is not the end state,
`verified` is. Post-deploy, `verify-to-prod` runs the health pulse and the
incident's own regression test against production; two consecutive reds roll
the deploy back automatically and open a `post-mortem` issue. This is the
cheapest oracle in the estate because it already exists, and it is genuinely
held out: no amount of PR-time cleverness can reach a signal that is only
produced after the merge it would need to influence.

**7d. Deterministic analysis stack (your Rust list, corrected to your code):**

| concept | Python estate | .NET estate | Rust (if/where you have it) |
|---|---|---|---|
| lint/idioms | `ruff` + format check | Roslyn analyzers, warnings-as-errors | Clippy |
| types/UB | `pyright --strict` | compiler + nullable enabled | Miri |
| dep security | `pip-audit`, `deptry` | `dotnet list package --vulnerable` | cargo-audit / cargo-deny |
| properties | `hypothesis` on risk paths | property tests | Kani |
| tool sandbox | Hermes Docker terminal backend: pinned image, `:ro` mounts, explicit env forwarding | same | (WASM fuel-metering isn't a Hermes feature; container isolation is) |

All of it as branch-protection requirements beside the evidence gate. The
agent's PR clears them or it doesn't; nothing to argue with, and nobody to
argue with — that is now the whole promotion decision.

**What you gave up, stated plainly so it can be disagreed with.** The tap
caught things no gate encodes: a technically-correct change that was the
wrong idea. Auto-merge cannot catch that class and does not pretend to. The
trade is that the tap was also a queue — work sat waiting on your attention,
which is the scarcest thing here — and the failure it prevented is now
handled by being *cheap to reverse* rather than *hard to reach*: revert is a
PR, rollback is automatic, and `no-auto` on a path buys the old behaviour
back for anything you decide is worth the wait.

---

<a id="s8"></a>

## 8. NEVER TWICE — the lesson ladder

The research reality first: agents follow retrieved experience — similar
input + retrieved memory ⇒ similar output — so a *wrong* stored lesson
propagates and compounds. Naive "remember your mistakes" makes some agents
worse. The design answers with two mechanisms:

**A. The ladder.** Every closed incident's lesson is compiled to the highest
rung it admits. Rungs 0–2 reduce probability; only 3–4 guarantee.

| rung | form | applied by | guarantee |
|---|---|---|---|
| 0 | episodic record (session DB, issue) | automatic | none — raw material |
| 1 | **`SKILL.md` entry, admitted by replay (B)** | automatic | probabilistic, measured |
| 2 | checklist step in `pr-discipline` / `incident-triage` | per-task check | probabilistic, targeted |
| 3 | **incident-named regression test** (`test_incident_0042_*`), lint rule, CI check, pre-commit hook | CI, 100% | deterministic |
| 4 | structural impossibility — permission removed, schema/type constraint, config made invalid | architecture | can't recur by construction |

Examples: "agent pushed to main" → rung 4 (revoke main push — mistake now
impossible). "Pool exhaustion misdiagnosed as app bug" → rung 3 (regression
test asserting pool metrics in the triage output) + rung 2 (triage checklist:
check pool before app). "Vague instruction caused a detour" → rung 2
(restate-and-confirm in USER.md) — admits no higher rung; accept probability
reduction and monitor.

**B. Admission control on lessons — automatic, and by measurement.** Nobody
hand-edits a law here. Skills accumulate on their own, and what decides
whether an entry lives is a replay over the incident corpus, not a review.

Lessons still never free-write. The path is:

```
incident closed
  → post-mortem skill emits {candidate lesson, its eval case}
  → candidate written to the owning SKILL.md on a branch
  → REPLAY: run the whole corpus with and without the candidate
  → admitted iff  (a) batch score improves, AND
                  (b) no case that previously passed now fails, AND
                  (c) it also improves on the 20% holdout it never saw
  → green CI (§7) merges it. No tap.
  → rejected candidates are recorded with their numbers, so the same
    lesson is never re-proposed and re-measured on a later incident
```

Condition (b) is not redundant with (a). An aggregate that improves can hide
a regression on a case that used to pass, and a lesson that trades one class
of correct behaviour for another is exactly the "wrong stored lesson
propagates" failure this section opens with. Aggregate improvement is the
cheapest thing to game and the easiest to mistake for progress.

**Deletion is automatic too, and it is the half that matters.** Every active
lesson is re-scored on the weekly replay. One that has stopped paying is
deleted by the run that proves it, with the numbers in the commit message —
not argued about on a Sunday. Deletion is a safety operation, per the ACL
findings and MNL's update-only-on-improvement rule, and an accumulator with
no eviction is just a context bill that grows until it degrades the thing it
was meant to improve.

**What keeps this from being a closed loop.** The agent writes the lesson and
the agent runs the replay, so the corpus is the only thing standing between
accumulation and self-congratulation. Per §7b it is append-only and CI
enforces that: a lane may add an incident case, and may never edit or delete
one. Twenty percent is held out of context entirely. A lesson that only
improves the cases the agent could read is not admitted.

WORK's dispatch keeps its pre-flight: session_search + grep the incident test
suite for similar signatures — "have we failed this way before?" — the
deterministic version of the pre-commit-gate pattern now appearing in the
wild.

**C. The eval corpus (`estate-evals/incidents.jsonl`)** — every resolved
incident becomes selection pressure:

```jsonl
{"id":"inc-0042","date":"2026-08-21","source":"watch",
 "input":{"summary":"5xx spike on engine","evidence":["fly logs excerpt","metric window"],"issue":"#312"},
 "ground_truth":{"root_cause":"pg pool exhaustion via leaked conns in job X","fix_pr":"#315",
                 "verified_by":["pytest tests/incidents/test_incident_0042.py","health 200 post-deploy"]},
 "lesson":{"text":"check pool saturation before app-level hypotheses",
           "rung":"3+2","artifact":"test_incident_0042_pool.py","status":"active"}}
```

Correct triage on these cases = the private-ish score for §9's nightly loop.
Mistakes are the most valuable eval data you own.

---

<a id="s9"></a>

## 9. RSI — three loops

**Loop 1 — experiential (on by default, day 0).** Skill auto-creation fires
after successful complex tasks (5+ tool calls), security-scanned on write;
the Curator reviews agent-created skills weekly after idle
(keep/patch/consolidate/archive, REPORT.md in `~/.hermes/logs/curator/`).
Config: leave on; the §4 git snapshot is the safety. The Curator's verdicts
now apply themselves through §8B's replay rather than waiting to be read —
keep/patch/consolidate execute on a measured improvement, archive executes on
a measured decline, and REPORT.md becomes the record of what already happened
instead of a list of things somebody has to action.

**Loop 2 — evolutionary (install day 0, nightly, offline).**

```bash
git clone https://github.com/NousResearch/hermes-agent-self-evolution.git
cd hermes-agent-self-evolution && pip install -e ".[dev]"
export HERMES_AGENT_REPO=~/.hermes/hermes-agent
# tonight (bootstrap):
python -m evolution.skills.evolve_skill --skill incident-triage \
       --iterations 10 --eval-source synthetic
# steady state — sessiondb reads Claude Code, Copilot AND Hermes history;
# your existing Claude Code sessions are eval data you already own:
python -m evolution.skills.evolve_skill --skill incident-triage \
       --iterations 10 --eval-source sessiondb
```

~$2–10/run, API-only. Upstream gates: tests 100%, ≤15KB skills, caching
compatibility, semantic preservation, **PR — never direct commit**. The PR
stands; the review does not. An evolution PR clears the same §7 checks as any
other, plus §8B's replay including the holdout, and merges itself. Cron it
nightly rotating your three most-used skills; as §8's corpus grows, it becomes
the eval source that matters. Mornings you read nothing — the skills that
improved are already in, the ~9/10 that did not are recorded as rejected with
their numbers.

**Loop 3 — the honest boundary.** In the official pipeline only Phase 1
(SKILL.md evolution) is implemented; tool descriptions, system-prompt,
tool-code evolution, and the continuous unattended pipeline are planned.
AIDE²-class continuous self-rewriting is not a Hermes feature today; the
nightly cron is the real approximation, and per Weco, expect ~9/10 of
proposals to deserve rejection — a high rejection rate means the gates work.

---

<a id="s10"></a>

## 10. Leash (week 1, all six before WORK gets write access)

1. Hard USD cap per task, kill-without-asking (degenerate loops run to
   hundreds over a quiet weekend).
2. Egress allowlist on every network-touching tool — the single
   highest-leverage control against injection via logs/issues/email.
3. Append-only tool-call audit log somewhere the agent can't write.
4. Secret hygiene: session DB indexes whatever the agent read; prod creds
   tool-scoped only.
5. Promotion gates everywhere self-modification happens — now mechanical, not
   manual: skills via git snapshot + §8B replay; lessons via replay against an
   append-only corpus with a 20% holdout; evolution via upstream PR flow into
   the same gates. No lane may write to what grades it (§7b).
6. Auto-healing is a fixed catalogue, never a judgement (§5): pre-authorised
   scripts, an idempotent verify, a per-signature cap, and escalation the
   moment the cap is hit.

---

<a id="s11"></a>

## 11. Cost model (estimates — validate week 1 via `/usage`; caps are the guarantee, estimates aren't)

| lane | driver | order of magnitude |
|---|---|---|
| WATCH | 15-min pulses, cheap model, short contexts | $10–40/mo |
| WORK | per-issue, frontier, $5 hard cap | $0.50–5/issue |
| evolution | nightly GEPA | $60–300/mo |
| meta-cognition | skill extraction, nudges, session summaries | pin to cheap aux lane or it can exceed the work bill |
| VPS | | $5–20/mo |

---

<a id="s12"></a>

## 12. Build order

- **Hour 1** install, doctor, Telegram, approvals verified, two profiles.
- **Hour 2** seed MEMORY.md + USER.md (terse).
- **Hour 3** prune skills; per-platform gating.
- **Hour 4** skills→git + hourly drift commit; state.db backup cron.
- **Day 1** `estate-map` + `incident-triage`; WATCH pulse. Digest starts
  exception-only from the first day — a quiet estate has never sent a
  message here, so nobody ever learns to skim one.
- **Day 2** install self-evolution; one synthetic run; read report; schedule
  nightly. Create `estate-evals/` with the JSONL schema **and the append-only
  CI check on it** — the corpus is load-bearing from the moment it exists.
- **Week 1** leash ×6; evidence gate + static gates in CI; board + labels;
  skills 3–6 including `post-mortem`; first two remedies in the heal
  catalogue (restart machine, recycle worker) with caps.
- **Week 2** WATCH opens threshold issues and heals the catalogued ones;
  consider memory provider; first `ready` on something that can't hurt
  (flaky test, docs drift — which also attacks your docs-out-of-sync pain:
  WORK diffs docs against code as a recurring `proposal`).
- **Week 3** WORK live, **still merging by hand**; first full
  issue→PR→merged→deployed→verified cycle; first post-mortem through the
  ladder; §8B replay running in report-only mode beside it.
- **Week 4 — auto-merge on, and not before.** Three things must be true
  first, in this order, because each one is what makes the next safe:
  1. §7b holds — `CODEOWNERS` on `ci/` and `.github/`, branch protection you
     cannot reach from any lane's token, corpus append-only in CI. Prove it
     by trying to push a gate edit from WORK's token and getting refused.
  2. §7c holds — `verify-to-prod` has rolled a bad deploy back on its own at
     least once, in a drill you caused deliberately.
  3. §8B's replay has agreed with the hand-merge decision for a full week in
     report-only mode. If the gate would have merged something you rejected,
     that is a missing check, and the week restarts.

  Turn it on for one path first (docs, or the flaky-test lane), widen when it
  is boring. Reversing is one setting, which is the point of doing it this way
  round.
- **Month 2** sessiondb becomes primary eval source; incident corpus feeds
  evolution; review whether any rung-1/2 lesson recurred → promote it.
- **Sunday ritual (phone, ~25 min)** Curator report · skills git log ·
  `/journey` prune + lesson quality review (delete what didn't help) ·
  merge/reject evolution PRs · reorder the board.

---

<a id="s13"></a>

## 13. Out of scope v1 — on purpose, not "later"

A third *always-on* profile
(BENCH §6b is on-demand and label-drained, which is why it's allowed; the
six-profile production study stands: degradation hits all profiles
near-synchronously through shared substrate — a fleet is one mind with more
hands) · continuous
unattended self-rewriting (doesn't exist in Hermes; see §9) · anything
touching the prod database · rebuilding what Hermes ships (gateway, cron,
memory, skills, session search, subagents, approvals, provider fallback).

---

<a id="s14"></a>

## 14. Research appendix — claim, source, grade

| claim | source | grade |
|---|---|---|
| Hermes ships gateway/cron/memory/skills/subagents/approvals; skills auto-create after 5+ tool-call successes; Curator lifecycle | Nous docs + repo | **V** |
| Memory: MEMORY.md/USER.md char-capped no-auto-compact; SQLite+FTS5 sessions; 8 providers, single slot; one-writer-per-home warning | Nous docs | **V** |
| Self-evolution repo: DSPy+GEPA, $2–10/run, eval-source synthetic/sessiondb (Claude Code, Copilot, Hermes), gates incl. PR-only, Phase 1 only implemented | repo README | **V** |
| AIDE²: private-score selection, ~9/10 rejected, 16× context cut, emergent anti-reward-hacking, broken defence layer in lineage, Level 1 not ignition | Weco article (read in full) | **V** |
| Experience-following: error propagation from stored mistakes; deletion + selective addition; outcomes as free quality labels | ACL 2026 (Xiong et al.) | **V** (abstract-level) |
| MNL: mistake notes admitted only on batch improvement | arXiv | **V** (abstract-level) |
| Cron sessions pass `skip_memory=True`; memory providers intentionally do not run during cron | Hermes `AGENTS.md:1047`, `run_agent.py:404` | **V** |
| Six-profile synchronous degradation | arXiv reliability study | **R** |
| Meta-cognition cost can exceed work cost; session index = secrets archive; no queryable audit log | independent reviews | **R** |
| GitHub-as-orchestrator; two-profile split; lesson ladder; evidence gate; admission control wiring; cost ranges | this spec | **D** |
| Auto-merge on green CI, with the tap reduced to `no-auto`/`held` | this spec (2026-08-22 revision) | **D** — *unvalidated*: the failure it names is a correct change that was the wrong idea, and no gate in §7 detects that class. §12 week 4 is the drill that has to pass before it goes on |
| Held-out scoring (§7b/§7c) as the replacement for the tap's asymmetry | AIDE² private-score result, applied | **D, grounded in [V]** |
| Auto-healing from a fixed remedy catalogue rather than an LLM decision | this spec | **D** — the cap, not the catalogue, is the load-bearing part; a remedy that keeps firing is a defect being hidden |

Grade **D** items are choices, and they're falsifiable: each names the
failure it prevents. Attack those, not the vibes.

---

<a id="s15"></a>

## 15. What you actually write — honest count (founder correction, 2026-08-22)

The earlier closing line ("six SKILL.md files, two CI workflows, five cron lines")
undersold it. The accurate version:

- **Evidence gate (§7)** — real JavaScript in a GitHub Action. The ~20 lines in this
  spec are the naive version. The moment WORK games it (claims with no matched output,
  or output blocks that are echoed nonsense rather than real command results) you are
  editing that logic. A small living script, revised a few times.
- **Screenshot-to-issue handler (week 4)** — a genuine little program. Receive a Telegram
  image, call a vision model, format a user story, open a GitHub issue via API. Real
  Python with auth, retries and malformed input handling. Forty lines that behave like
  forty lines.
- **The six SKILL.md files** — Markdown, but `incident-triage` and `verify-to-prod` embed
  the actual shell commands that investigate this platform and check prod health.
  Getting those right for our Fly.io setup is engineering; the prose wrapper is the easy
  part. Any skill that shells out to a `scripts/` helper means writing that helper too.
- **Static-analysis gates** — not authored from scratch, but real work: wiring
  ruff/pyright/Roslyn into CI, tuning what fails the build, fixing the first wave of
  things they flag in existing repos.
- **The maintenance tail** — the part previously underweighted. Hermes ships fast and
  breaks defaults between releases. Our glue will break against upgrades. Community
  skills drift or go unmaintained. A cron fails silently and gets debugged. That is
  ongoing engineering, and "configuration" undersells it.

Realistically: a few hundred lines across those pieces — Python, YAML and JS — plus
steady upkeep.

**Why the trade still holds, stated so it can be disagreed with:** the code we write sits
at seams we control — the gate, the handler, the skills — and none of it is load-bearing
infrastructure that fails at 1am while you are at the park. When the evidence-gate script
breaks, a PR does not merge; the platform stays up. That property is what we are paying
for. Not "zero code", which was never honestly on offer. Anyone selling a genuinely
no-code version of this is making the claim to distrust.

*Not built here: the agent, the harness, the memory system, the orchestrator. Those
genuinely ship, and that is where the saving is real. What we build is the connective
tissue between them — and connective tissue is where integration projects bleed time.*

---

<a id="s16"></a>

## 16. Consult — a second mind on tap (founder request, 2026-08-22)

A lane that is stuck does one of two things today: it guesses, or it stops and
waits for the founder. The first spends money on a wrong answer, the second
spends a day. There is a third, and it costs nothing.

**The service is outside this repo, on purpose.** It runs on the founder's
laptop and holds the credential for whatever second model is available. This
estate never holds that credential, never manages that session, and never
starts that process. It knows one URL and one bearer token, and both are
optional. That is the whole coupling, and it is what lets the estate move to a
VPS without moving the consult with it.

**Exit 3 is the normal state, not a fault.** The laptop sleeps. Nothing in
either lane may block on a consult, retry it, or escalate when it is missing.
When there is no consult, the open question becomes a comment on the issue, in
line with principle 2 — all inter-agent state flows through the board — and the
lane carries on with the work it can still do.

**An answer is evidence, and the weakest kind.** The model answering cannot see
this machine. It will name flags, files and services that are not here, which
is not a defect of the model but of the question. Principle 3 does not bend for
it: a claim exists with a command and its output, and a consult produces
neither. When a consult and a command disagree, the command is right.

**Which model answers is not fixed, and the lane must not care.** The service
tries several in order and the first one that is ready and not benched takes
the question. On 2026-08-22 that meant a subscription model, then a 7B model
running on the laptop with no network at all. A backend that fails three times
in a row sits out for ten minutes, so a spent quota degrades the answer instead
of stopping the call. The reply names the backend that produced it. A lane that
would only accept an answer from one particular model is asking for a provider,
not a consult, and that is the out-of-scope case below.

A backend that cannot fail fast is worse than no backend, and one was removed
on 2026-08-22 for exactly that: its CLI retried a rate-limit internally instead
of returning it, so it spent its whole time cap on every consult and then
failed anyway. Anything added to the cascade carries a time cap.

**Three uses, no others.** Two failed attempts, a close design decision, or
anything about to be done that cannot be undone. Never for a question a command
would answer. Never twice on one problem.

**No secret leaves in a question.** The text goes to a third party. Tokens,
connection strings and the contents of `.env` are named, never pasted. This is
the one rule here that is not a matter of judgement.

Costs nothing per call in the shipped configuration, so §11's caps do not move.
Out of scope: making consult a provider in the model slot. Hermes already ships
provider fallback (§13), and a second opinion is not a fallback — it is a
different question asked on purpose.
