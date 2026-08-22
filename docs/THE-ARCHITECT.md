# THE ARCHITECT — master build spec
### Autonomous engineering estate on Hermes · GitHub · your phone

Supersedes `phone-run-estate.md` and `hermes-day0-config.md` (both folded in).
Every factual claim is graded in §14: **[V]** verified against primary source,
**[R]** reported by credible secondary source, **[D]** my design decision.

---

<a id="s0"></a>

## 0. Principles (each one earned, not aesthetic)

1. **The orchestrator is not an agent.** GitHub Projects is the queue, labels
   are the state machine, PRs are the output, your tap is the promotion gate.
   An LLM polling a queue is Opus tokens spent on `ORDER BY`. [D]
2. **Two minds, one board, no shared memory.** Cross-agent memory writes fail
   silently in Hermes cron contexts, and two writers on one memory compound
   into state nobody authored. All inter-agent state flows through GitHub
   issues/comments. [V]
3. **Verification by execution, never by introspection.** A claim exists only
   with a command and its output attached. Self-checking techniques are the
   weak form; CI is the strong form. [D, grounded in AIDE² results]
4. **Selection beats instruction.** Honesty, efficiency, and non-recurrence
   are properties you get from what survives your gates, not from prompt
   adjectives. The agent optimises whatever you actually merge. [V — Weco]
5. **Lessons compile downward.** A lesson in prose is probabilistic; a lesson
   as a test is deterministic. Every incident pushes its lesson as far down
   the ladder as it admits (§8). [D, grounded in experience-following research]
6. **Less context per role, more attempts.** The winning evolved agent cut
   context 16× per operator and reinvested tokens as search steps. Seed
   memory tersely; give each profile only what its role needs. [V — Weco]

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
        ◄── merged PRs only ────── you (one tap)
```

States on the board: `triage → agent-go → in-progress → pr-open → merged →
deployed → verified → closed`, plus `proposal` and `post-mortem`. Labels are
applied by WATCH (opening), you (`agent-go`, one tap), and WORK (the rest).

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
Cheap model lane. Its entire output is *issues with evidence*; it never fixes.

Concrete wiring for your platform (adjust app names):

| cadence | job | mechanics |
|---|---|---|
| 15 min | pulse | `fly status -a prospector-engine`, `fly machine list`, health endpoints, error-rate window from `fly logs --no-tail` |
| on threshold | investigate | correlate log window ± recent deploys (`gh run list`, `git log`) ± past incidents (session_search + closed issues) → open issue labelled `triage`, evidence inline |
| 07:00 | digest | what changed, what's degraded, open threads → Telegram |
| Sun | proposals | 3 unasked-for improvements as `proposal` issues, each with the measurement that would prove it worked |

Issue template (enforced by the skill): claim → query run → raw output →
time window → similar past incidents. No evidence, no issue. Thresholds
live in `estate-map` so tuning them is a skill edit, not code.

Untrusted-input note: logs are attacker-influenceable text. WATCH's egress
allowlist (§10) is the control — constrain what it can reach, don't try to
make it un-foolable.

---

<a id="s6"></a>

## 6. WORK — hands (week 3, after the leash)

Trigger: `agent-go` label (your tap — the entire v1 safety model).
Frontier lane. Repo write via worktree only; no push to main, no deploy
rights, no prod DB, no prod secrets in context.

Loop: read issue + all comments (its briefing — no memory handoff) →
one-line interpretation posted; low confidence = wait → worktree →
**reproduce first** (cannot reproduce = comment and stop; never fix an
unseen bug) → fix → verify by execution → PR with Evidence block →
plain-English comment + Telegram ping → stop. Never merges.

Follow-through (`verify-to-prod`): after your merge and deploy, WORK
re-checks health + the incident's regression test in prod context, posts
post-deploy evidence, moves issue to `verified`, then `closed`. "Tracks to
shipped" is a state transition it owns, not a vibe.

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

**Evidence gate — CI, mechanical, non-negotiable:** every PR body needs
`## Evidence` with one `### Claim:` per claim, each followed by the command
and real output. The workflow from the earlier sheet stands (counts claims
vs output blocks; fails on mismatch). Corollary: CI results are the score
the agent can see; your merge is the score it can't. Keep that asymmetry.

**Deterministic analysis stack (your Rust list, corrected to your code):**

| concept | Python estate | .NET estate | Rust (if/where you have it) |
|---|---|---|---|
| lint/idioms | `ruff` + format check | Roslyn analyzers, warnings-as-errors | Clippy |
| types/UB | `pyright --strict` | compiler + nullable enabled | Miri |
| dep security | `pip-audit`, `deptry` | `dotnet list package --vulnerable` | cargo-audit / cargo-deny |
| properties | `hypothesis` on risk paths | property tests | Kani |
| tool sandbox | Hermes Docker terminal backend: pinned image, `:ro` mounts, explicit env forwarding | same | (WASM fuel-metering isn't a Hermes feature; container isolation is) |

All of it as branch-protection requirements beside the evidence gate. The
agent's PR clears them or it doesn't; nothing to argue with.

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
| 1 | distilled lesson (MEMORY.md / skill note) | model recall | probabilistic |
| 2 | checklist step in `pr-discipline` / `incident-triage` | per-task check | probabilistic, targeted |
| 3 | **incident-named regression test** (`test_incident_0042_*`), lint rule, CI check, pre-commit hook | CI, 100% | deterministic |
| 4 | structural impossibility — permission removed, schema/type constraint, config made invalid | architecture | can't recur by construction |

Examples: "agent pushed to main" → rung 4 (revoke main push — mistake now
impossible). "Pool exhaustion misdiagnosed as app bug" → rung 3 (regression
test asserting pool metrics in the triage output) + rung 2 (triage checklist:
check pool before app). "Vague instruction caused a detour" → rung 2
(restate-and-confirm in USER.md) — admits no higher rung; accept probability
reduction and monitor.

**B. Admission control on lessons.** Lessons never free-write into memory:
- entry is via the `post-mortem` skill output → a PR you tap (a wrong lesson
  compiled to rung 3 is a *permanent wrong constraint*; the tap is cheap
  insurance);
- each lesson carries its eval case, and future outcomes are its quality
  labels — a lesson that doesn't demonstrably help gets **deleted** at the
  Sunday review (deletion is a safety op, per the ACL findings and MNL's
  update-only-on-improvement rule);
- WORK's dispatch includes a pre-flight: session_search + grep the incident
  test suite for similar signatures — "have we failed this way before?" —
  the deterministic version of the pre-commit-gate pattern now appearing in
  the wild.

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
Config: leave on; the §4 git snapshot is the safety; read the report Sundays.

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
compatibility, semantic preservation, **PR review — never direct commit**.
Cron it nightly rotating your three most-used skills; as §8's corpus grows,
it becomes the eval source that matters. Morning: evolution PRs with coffee.

**Loop 3 — the honest boundary.** In the official pipeline only Phase 1
(SKILL.md evolution) is implemented; tool descriptions, system-prompt,
tool-code evolution, and the continuous unattended pipeline are planned.
AIDE²-class continuous self-rewriting is not a Hermes feature today; the
nightly cron is the real approximation, and per Weco, expect ~9/10 of
proposals to deserve rejection — a high rejection rate means the gates work.

---

<a id="s10"></a>

## 10. Leash (week 1, all five before WORK gets write access)

1. Hard USD cap per task, kill-without-asking (degenerate loops run to
   hundreds over a quiet weekend).
2. Egress allowlist on every network-touching tool — the single
   highest-leverage control against injection via logs/issues/email.
3. Append-only tool-call audit log somewhere the agent can't write.
4. Secret hygiene: session DB indexes whatever the agent read; prod creds
   tool-scoped only.
5. Promotion gates everywhere self-modification happens: skills via git
   snapshot + Sunday review; lessons via post-mortem PR; evolution via
   upstream PR flow.

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
- **Day 1** `estate-map` + `incident-triage`; WATCH pulse + 07:00 digest.
- **Day 2** install self-evolution; one synthetic run; read report; schedule
  nightly. Create `estate-evals/` with the JSONL schema.
- **Week 1** leash ×5; evidence gate + static gates in CI; board + labels;
  skills 3–6 including `post-mortem`.
- **Week 2** WATCH opens threshold issues; consider memory provider;
  first `agent-go` on something that can't hurt (flaky test, docs drift —
  which also attacks your docs-out-of-sync pain: WORK diffs docs against
  code as a recurring `proposal`).
- **Week 3** WORK live; first full issue→PR→merged→deployed→verified cycle;
  first post-mortem through the ladder.
- **Month 2** sessiondb becomes primary eval source; incident corpus feeds
  evolution; review whether any rung-1/2 lesson recurred → promote it.
- **Sunday ritual (phone, ~25 min)** Curator report · skills git log ·
  `/journey` prune + lesson quality review (delete what didn't help) ·
  merge/reject evolution PRs · reorder the board.

---

<a id="s13"></a>

## 13. Out of scope v1 — on purpose, not "later"

Auto-merge (your tap IS the safety model) · a third *always-on* profile
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
| Cron cross-agent memory writes fail silently (skip_memory hardcoded) | arXiv "channel fracture" | **R** |
| Six-profile synchronous degradation | arXiv reliability study | **R** |
| Meta-cognition cost can exceed work cost; session index = secrets archive; no queryable audit log | independent reviews | **R** |
| GitHub-as-orchestrator; two-profile split; lesson ladder; evidence gate; admission control wiring; cost ranges | this spec | **D** |

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
the question. On 2026-08-22 that meant a subscription model, then a free-tier
one, then a 7B model running on the laptop with no network at all. A backend
that fails three times in a row sits out for ten minutes, so a spent quota
degrades the answer instead of stopping it. The reply names the backend that
produced it. A lane that would only accept an answer from one particular model
is asking for a provider, not a consult, and that is the out-of-scope case
below.

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
