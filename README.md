# The Architect

An engineering agent that watches your production estate, tells you when it
breaks, and opens pull requests you approve from your phone.

It is one directory. You clone it, answer five questions, and it runs. Point it
at a different estate and it is a different agent, with no code changes.

**The spec is [`docs/THE-ARCHITECT.md`](docs/THE-ARCHITECT.md).** Every one of
the 133 rows in `REQUIREMENTS.jsonl` carries a `spec` field that links to the
section it came from, so any requirement can be traced back to the paragraph
that asked for it in one click. Nothing is in this repo that the spec did not
ask for.

---

## Setup

```bash
git clone <this-repo> my-agent
cd my-agent
./install
```

`./install` asks five questions, installs the agent, writes your answers into
every file that needs them, and then proves the result works. It is safe to run
twice.

It needs a way to talk to a model, and there are two. Either an API key from
[console.anthropic.com](https://console.anthropic.com/settings/keys), which
goes in `.env`. Or your existing Claude Code subscription, which it reuses:

```bash
./bin/hermes auth login
```

The second one costs nothing extra and is what the estate that built this uses.
`./install` asks for a key and accepts an empty answer if you would rather log
in.

Check what your machine is missing before you start:

```bash
./install --check      # looks at prerequisites, changes nothing
```

Set one up without being asked anything — for a server, or a second machine:

```bash
cp estate.example.yaml my-estate.yaml    # edit it
ANTHROPIC_API_KEY=sk-... ./install --estate my-estate.yaml
```

Nothing is scheduled until you say so. When you are ready:

```bash
./bin/hermes gateway install
```

---

## What you get

Two lanes, on purpose. They have different powers because they carry different
risk.

**WATCH** reads and reports. It probes your services every 15 minutes, maps the
estate every hour, and sends a digest at 07:00. It can open issues. It cannot
write code, deploy, or merge anything. It runs on the cheapest model, because
noticing is cheap.

**WORK** writes code. It only ever works in a git worktree and only ever
finishes by opening a pull request. It cannot push to your main branch and it
cannot deploy. Every change it makes waits for you.

Promotion between them is a label you add on GitHub, from your phone. That is
the whole control surface.

---

## What is switched on

Everything in this repo is built. A `features:` block in `estate.yaml` decides
what actually runs, and only the two lanes that cannot write anything ship on.

```bash
bin/features                 # what is on right now
bin/features work on         # switch one on
bin/features evolution off   # switch one off
```

```
  lane                 state  spec   what it does
  watch                   ON  §5     15-minute pulse, hourly estate map, 07:00 digest
                                     reads only, never writes. The free lane.
  sunday_rituals          ON  §8     Sunday review and three proposal issues
                                     opens issues, merges nothing.
  work                   off  §6     the agent-go label opens pull requests
                                     WRITES to your repo. Never merges. Turn on last.
  evolution              off  §9     nightly skill evolution, lands as a pull request
                                     COSTS MONEY, $2-10 a night.
  bench                  off  §6b    the on-demand local lane on this laptop
                                     runs nothing by itself. You start it.
  screenshot_handler     off  §15    a Telegram photo becomes a GitHub issue
                                     needs TELEGRAM_BOT_TOKEN.
```

Off means no cron job exists for that lane, so it cannot fire, cost anything or
touch your repository. That is why the switch is worth having: a lane you have
not proved yet is inert rather than merely discouraged.

Turn one on, run `bin/verify`, then turn on the next.

---

## What it costs

Measured on this build, not estimated:

| | |
|---|---|
| one WATCH agent run | **$0.0298** |
| WATCH, running the full schedule | **$22.63/month** |
| the 15-minute health pulse | **$0** — it is a shell script, not a model |
| the same estate on Opus instead of Haiku | $113.15/month |

The single biggest cost in an agent like this is the model waking up to do
something a `curl` could do. The health pulse used to be an agent task at
$0.0298 a run — $85.92/month to check two URLs. It is now `bin/pulse.sh`,
scheduled with `--no-agent`, and it costs nothing.

See it for yourself any time:

```bash
./bin/cost-report.sh
```

Two guards keep this honest. `bin/check-models-priced.py` refuses any model that
is not in the shipped price table, because a model nobody can price spends money
invisibly. And `limits.max_cost_usd_per_task` in `estate.yaml` is a hard stop,
not a warning.

---

## The only file you edit

`estate.yaml`. Everything else is generated from it.

```yaml
estate:
  name: acme

github:
  owner: your-username
  repo: your-repo

services:
  - key: api                                  # short handle used in reports
    app: acme-api                             # the platform's app name
    url: https://api.acme.example.com/health      # what gets probed
    expect: "2xx"                             # 2xx | 3xx | any-answer
    note: ""                                  # what a human should know

# Anything else the agent may reach. The services above are added for you.
egress_extra: []

models:
  watch: claude-haiku-4-5
  work: claude-haiku-4-5
```

Add a service, change a model, point it at a different repo — then:

```bash
bin/render      # writes your change into every generated file
bin/verify      # proves the result still works
```

`bin/render --check` fails if anyone has hand-edited a generated file, so the
estate and the files describing it cannot quietly drift apart.

No template may name one of your services. A template that wrote
`{{ service.api.app }}` would build perfectly for the estate it was written in
and fail on the next one, so `bin/render` refuses it and tells you to loop
instead:

```
{{#services}}{{app}} answers on {{url}}
{{/services}}
```

Anything a human needs to know about one service — that it sits behind a login,
that a 404 there is normal — goes in that service's `note`, which the skills and
memory files pick up. That is the only place estate-specific knowledge lives.

---

## Every generated file

Twenty-one files are written by `bin/render` from `estate.yaml`. Do not hand-edit
them — `bin/render --check` fails if you do, and your edit is lost the next time
anyone runs `bin/render`. Change the template in `templates/` instead, or change
`estate.yaml`.

Three of them end `.seed` in `templates/`. Those are starting points the agent
then edits for itself: they are written once and never rendered over, so what it
learns is never overwritten by a template.

<!-- files: column 1 is checked against templates/ by bin/check-readme.py -->

| file | what it does |
|---|---|
| `CUTOVER.md` | The order in which the old estate is replaced, one reversible step at a time, and what is still waiting on a founder decision. |
| `MEMORY.md` | What the agent knows about your platform: the service table, what is normal, what a surprise looks like. Seeded once, then the agent's own. |
| `REQUIREMENTS.jsonl` | Every requirement as one JSON line with a shell command that proves it. This is the ledger `bin/check-requirements.py` runs. |
| `RITUALS.md` | The two recurring reviews — Sunday review and Sunday proposals — and what each must produce. The `sunday-*` cron jobs read this. |
| `bin/prove-watch-readonly.sh` | Proves WATCH cannot write code by attempting a write and requiring it to fail. Needs the read-only PAT before it can run. |
| `bin/pulse.sh` | The free health check. Probes every service in `estate.yaml` and prints nothing when all are healthy. No model, no cost. |
| `cron/watch.jobs` | The WATCH schedule, one JSON job per line. `bin/install-cron.py` reads this; `./install` runs it for you. |
| `cron/work.jobs` | The WORK schedule, same format. `./install` reads it but creates nothing while the `work` feature is off, which is how it ships. |
| `egress-allowlist.txt` | Every host this estate is allowed to reach: your services, `egress_extra`, and the APIs it needs. Everything else is refused. |
| `handlers/screenshot_to_issue.py` | Turns a screenshot sent over chat into a well-formed GitHub issue, so a photo of a broken screen becomes work. |
| `profiles/watch/MEMORY.md` | The WATCH lane's own memory. Seeded from the same platform map, then diverges as that lane learns. |
| `profiles/watch/egress-allowlist.txt` | WATCH's narrower allowlist. Logs are attacker-influenceable text, so this is a control, not a matter of the agent's judgement. |
| `profiles/work/MEMORY.md` | The WORK lane's own memory, seeded the same way. |
| `scripts/pulse.sh` | The identical pulse script where the scheduler looks for it. `cron create --script pulse.sh` resolves in `scripts/`; `bin/` is where a human types it. |
| `bin/backup-state.sh` | Nightly `sqlite3 .backup` of `state.db`. It does not use `cp`, because copying a live database gives a torn file. The off-box copy is taken by the estate's own backup engine, which already holds the credentials and verifies every copy. |
| `scripts/bootstrap-age-auth.sh` | Encrypts the Claude credential to a file the repo can carry. Run once, by a person, on a machine that already holds the credential. |
| `skills/PLATFORM_GATING.md` | What each skill needs before it may run. A skill whose platform is missing must fail at the top, not half-run. |
| `skills/consult/SKILL.md` | Ask a different model when you are stuck, and treat the answer as the weakest evidence you hold. Never acts on it unchecked. |
| `skills/founder-mac/SKILL.md` | Run a command on the founder's Mac from the cluster through `mac-run`; prove the door, then use it, instead of saying there is no access. |
| `skills/estate-map/SKILL.md` | Print the current shape of the estate — apps, health, repos, open board. Run before guessing where anything lives. |
| `skills/incident-triage/SKILL.md` | Turn a red platform into one GitHub issue with real evidence in it. Never fixes anything. |
| `skills/phone-idea-flow/SKILL.md` | Rendered from `templates/skills/phone-idea-flow/SKILL.md.tmpl`: the phone idea flow with this estate's board repo filled in. |
| `skills/post-mortem/SKILL.md` | Close an incident by naming the class of mistake and adding a guard. Runs after the platform is serving again, never during. |
| `skills/pr-discipline/SKILL.md` | How a change is made: worktree, reproduce, smallest diff, pull request, stop. Never merges. |
| `skills/screenshot-to-story/SKILL.md` | Turn a photo the founder sends into a well-formed issue, when the message has an image and almost no words. |
| `skills/verify-to-prod/SKILL.md` | Prove a merged change is actually running in production, before anything is called done. |

<!-- /files -->

---

## Every file in this repo

Every tracked file, and every directory holding one, is listed below with what
it is for. `bin/check-readme.py` fails if a tracked path is missing from this
table, described twice, or listed with no description, so adding a file without
saying why it exists turns `./bin/verify` red. The count is in that command's
output rather than written here, because a number in prose is the first thing
to go stale.

Nothing generated appears here. Those are in the table above and none of them
are tracked, so what you see below is the source they come from.

<!-- tracked: column 1 is checked against `git ls-files` by bin/check-readme.py -->

| path | why it exists |
|---|---|
| `.claude/` | Session hooks for an agent working on this repo. Kept in the repo, not the machine, so a session on any computer or cloud runner starts with the same fences. |
| `.claude/settings.json` | One SessionStart hook: if the estate guards are not in `$HOME/.claude`, clone them from the claude-estate repo. A present checkout is left alone. Set `ESTATE_REPO` to point at a fork. |
| `.dockerignore` | Keeps the Docker build context to source. Without it the context is 1.2 GB, almost none of which the image needs, and every deploy pays for it. |
| `.env.example` | Every credential the agent can use, each with an empty value and a line saying what it unlocks. `./install` copies it to `.env`, which is mode 600 and never tracked. |
| `.estate/` | Standing decisions about this repository that a machine has to be able to find. One file per decision, and deleting the file withdraws the decision. |
| `.estate/public-ciphertext-ok` | Records that committing age ciphertext to a public remote is deliberate, names what is committed and what opens it, and states the cost accepted: a public git object is permanent, so the credential inside is treated as rotatable. |
| `.gitignore` | What must never be committed: the generated files, the agent's runtime state, and `.env`. Most of its lines were added after a `git add -A` swept live state into a commit. |
| `PINNED_VERSION` | The Hermes tag and commit this estate is known to work on. `bin/verify` fails when the running agent is a different commit, so an upgrade cannot happen by accident. |
| `CODEOWNERS` | Every top-level directory names the founder as reviewer, so a pull request touching any of them cannot merge without a review request going to him (crew#88). Generated from `git ls-tree`, one row per directory; a new directory without a row turns the crew#88 count red. |
| `DECISIONS.md` | Rulings that outlive the pull request that caused them. A decision here is binding until a later entry overrides it, so nobody relitigates one in a review. |
| `README.md` | This file. `bin/check-readme.py` fails the build when it stops describing what the repo ships. |
| `SOUL.md` | The agent's base identity, read before anything else on every run. It is upstream Hermes text and is deliberately not estate-specific. |
| `USER.md` | Who the agent works for and how to talk to them: tone, when to escalate, and the rule that nothing is done without the command output. Each lane overrides it. |
| `bin/` | Everything a human types. One command per file, each with a single job, none of them generated. |
| `bin/audit-append.sh` | Appends one line per tool call to a log kept outside this directory, so the record survives the estate being deleted. |
| `bin/check-models-priced.py` | Refuses any model that is not in the shipped price table. A model nobody can price spends money invisibly. |
| `bin/check-readme.py` | Fails when this README no longer lists every generated file, every cron job and every tracked path. It checks the half a machine can know. |
| `bin/check-requirements.py` | Runs the `acceptance_cmd` of all 133 requirement rows and writes the result to `logs/requirements-status.json`. A row closes on exit 0, never on anyone asserting it. |
| `bin/consult` | Asks a second model a question from inside a lane. Exit 3 when there is none, which is the normal state of a sleeping laptop and never an error. |
| `bin/cost-report.sh` | What the agent has actually spent, read out of the usage ledger rather than estimated. |
| `bin/curator-report.sh` | Weekly. Asks the skill curator what it makes of the skills and writes it to `logs/curator/REPORT.md`. It changes nothing; the report is an input to the Sunday review. |
| `bin/features` | The on/off switch. Reads and writes the `features:` block in `estate.yaml`, and `--check` gives the scheduler an exit code so an off lane gets no jobs. |
| `bin/hermes` | The wrapper you must always use. Bare `hermes` defaults to `~/.hermes`, which is a different estate; this sets `HERMES_HOME` to this directory. |
| `bin/install-cron.py` | Turns a `.jobs` file into scheduled jobs, idempotently. With `--feature` it creates nothing at all while that lane is off. |
| `bin/render` | The only thing that writes a generated file. It fills the templates from `estate.yaml` and refuses a template that names one of your services. |
| `bin/skill-drift-commit.sh` | Hourly. The agent edits its own skills, and without this a skill that quietly rewrote itself last Tuesday has no diff and no way back. |
| `bin/sync-scripts.sh` | Copies `bin/pulse.sh` into `scripts/`, because the scheduler refuses a symlink out of `scripts/`. It diffs afterwards so the two copies cannot drift. |
| `bin/teardown` | Stops everything firing and keeps the estate. `--all` also removes the agent and its dependencies. |
| `bin/check-platform.py` | Refuses an estate.yaml that names a platform the estate has left (fly.dev, flyctl, kind: fly). Incident 2026-08-26: three dead Fly rows sat in the live file two days after R1 and verify called them "stopped, as ordered". |
| `bin/verify` | The probe: one command that says whether the whole thing works, in a few seconds. `--full` also runs the requirement ledger. |
| `bin/verify-sovereign-plugin` | Loads `plugins/sovereign` the way the gateway does and exits 0 only when the seven `sb-*` commands and the photo hook are registered. `bin/verify` reads it as one row (crew#284 CP1). |
| `bin/verify-consult` | The same probe for the consult service (§16): daemon, loopback bind, 401 without a token, a live round-trip timed cold and warm, and the token never appearing in a log. Rows that cannot apply on this machine SKIP rather than fail, so it still runs off the founder's laptop. |
| `bin/shell-strict` | The estate shell standard (crew#620 CP3), checked by hand instead of trusted on faith: every `*.sh`/`*.bash` file (or a file starting `#!...sh`) must pass `shellcheck -S warning`, be `shfmt -d` clean, set `set -euo pipefail`, and install a `trap`. `bin/verify` and `bin/verify-consult` are the one named exception, kept on `set -uo pipefail` without `-e`: both are pass/fail report harnesses that must run every row even after one FAILs, and `-e` was verified to abort on a bare `[ ... ]`. |
| `ci/` | The gates that run on a pull request. Each one refuses a mistake rather than reporting it. |
| `ci/EVIDENCE_GATE_PROOF.md` | The evidence gate refusing ten dishonest pull request bodies and passing three honest ones, with the output. A gate nobody has watched refuse anything is a claim. |
| `ci/evidence-gate.js` | Reads the pull request body and rejects a claim with no command output behind it. Placeholder blocks, and blocks that only repeat the claim, are rejected too. |
| `.github/` | The CI this repo runs on itself. Not to be confused with `ci/`, which holds the workflows this repo installs into another one. |
| `.github/workflows/` | Five workflows. Everything a runner can honestly answer runs here on every pull request. |
| `.github/workflows/gates.yml` | Four jobs that mean the same thing away from the founder's laptop. `gates`: every template renders from `estate.example.yaml`, `check-readme.py`, and `verify-consult`. `bin/verify` is left out on purpose — it asks about the gateway, the venv and a credential, and a runner has none of them, so it would be red for being in the wrong place. `incident-tests`: the crew#182 phone-flow incidents against the pinned `hermes-agent` commit. `shell-strict`: crew#620 CP3's shell standard over every tracked shell file. `otto-tests`: `bin/otto-demo` runs the whole Otto v1 conformance suite (`otto/tests/`) fresh, with a real `nats-server` and a real Postgres+pgvector provisioned for the two sections that need one. This repo is public, so the minutes are free. |
| `.github/workflows/security-scan.yml` | The estate security gate, the same composite action every active repo runs (`chidionyema/idp/.github/actions/security-scan@main`): gitleaks over the full history, pip-audit, and npm audit at High+ on shipped dependencies only. It is installed by `idp/bin/estate-security-rollout`, so this file is the rollout's, not this repo's; edit it in idp. |
| `.github/workflows/operating-model-gate.yml` | The estate operating-model gate, called by name from idp (`chidionyema/idp/.github/workflows/operating-model-gate.yml@main`): grades the PR body against `idp/policy/operating_model.rego` and comments every refusal. The policy lives in idp; this file is a caller, so a rule change lands here without a commit. |
| `.github/workflows/stale.yml` | A copy of idp's `platform/github/workflows/stale.yml` (crew#504): an open pull request idle for a day is closed in the same hourly run, branch kept, with a message naming `gh pr reopen` and `Blocked-by:` as the two ways back. Issues are untouched. |
| `.github/workflows/wake-blocked.yml` | A copy of idp's `platform/github/workflows/wake-blocked.yml` (crew#504): every hour, a pull request closed by stale whose `Blocked-by:` line now points at a merged PR is reopened. |
| `ci/evidence-gate.yml` | The workflow that runs the gate: first the body check, then the check that a screenshot of the run is committed under `docs/evidence/pr-<n>/`. |
| `ci/static-gates.yml` | ruff, pyright strict, pip-audit and deptry as required checks on main. A red job here is not advisory. |
| `ci/tests/` | Tests of the gates themselves, because a gate that passes everything looks exactly like a gate that works. |
| `ci/tests/test_evidence_gate_gaming.py` | Thirteen pull request bodies, ten of them ways somebody tried to talk past the gate. It is what makes `EVIDENCE_GATE_PROOF.md` reproducible. |
| `config.yaml` | The agent's own settings: provider, default model, and `max_turns: 90` against an upstream default of 500, because a task needing more than 90 turns has gone wrong and should stop. |
| `cron/` | The scheduler's working directory. Only `.jobs` files belong to the repo; everything else in here is written while it runs and is ignored. |
| `cron/evolution.jobs` | The nightly skill-evolution schedule. Written by hand rather than rendered, because nothing in it varies from one estate to the next. |
| `deploy/` | Everything needed to run this estate somewhere other than the founder's laptop. Nothing in here runs locally. |
| `deploy/k8s/` | What the container does at boot on the cluster (crew#516 CP4). The manifests themselves live in `idp/platform/hermes-agent`. |
| `deploy/k8s/entrypoint.sh` | The image's entry point: copies the build (`/app/estate`) over the persistent `HERMES_HOME` volume, seeds `auth.json` once from `HERMES_AUTH_JSON`, renders `estate.yaml`, installs the WATCH and WORK lanes, then execs `gateway run`. State on the volume is never overwritten. |
| `deploy/k8s/boot-contract.sh` | The release contract (crew#736 CP2): before any image ships, CI boots it secretless as uid 10001 with a read-only root, imports every module in `boot-contract.txt`, and requires the agent card to answer within 90 seconds. An image that cannot boot never reaches the registry. |
| `deploy/k8s/boot-contract.txt` | The list of modules the image must import at boot, one per line with the reason each is load-bearing. A new dependency selection lands here or the contract fails the build. |
| `Dockerfile` | The hermes-agent runtime image for the Oracle OKE standby (crew#290/crew#286). Clones the pinned upstream commit itself at build time -- hermes-agent is a separate repo (gitignored here), not this repo's own source. |
| `.github/workflows/build-agent-image.yml` | Builds and pushes the Dockerfile above to GHCR (arm64-only, matching OKE's Ampere node pool), cosign-signed, same pattern as idp's build-multiarch.yml but self-contained here since a first unproven build shouldn't add blast radius to idp's shared pipeline. |
| `deploy/secrets/` | Ciphertext only. A cleartext credential has never been in this directory and the encryption is what makes it safe to track. |
| `deploy/secrets/claude-credentials.json.age` | The Claude credential encrypted to a key the platform holds, so a fresh container can decrypt it at boot without an agent ever carrying it. `scripts/bootstrap-age-auth.sh` writes it. |
| `docs/` | Written for a person to read, not for the machine to parse. |
| `docs/THE-ARCHITECT.md` | The spec. Every requirement row deep-links to the section it came from, so nothing is in this repo without a paragraph that asked for it. |
| `docs/claude-auth.md` | The whole credential chain, with links pinned to the exact upstream commit: where identity comes from, why the laptop's token cannot travel, and what to do when the fallback goes stale. |
| `docs/demo/` | One page per feature showing it running, with real pasted output under the command that produced it. Written for the founder, who did not build the thing and should not have to run it to find out whether it works. |
| `docs/demo/claim-gate.md` | The claim gate restamping a real `DONE:` as `UNVERIFIED:`, real run 2026-08-24: `stamp_unproven_done` against a throwaway ledger, pasted input and output side by side. |
| `docs/demo/telegram.md` | Button taps and a voice note handled by the gateway: the tap arriving as a callback, the note transcribed into the normal turn, with the 23-test gateway run pasted under the command that produced it. |
| `docs/demo/the-architect.md` | The gateway doing all three of its jobs, from a real run: `bin/verify` at 17 passed 0 failed, the two live sockets to the address `api.telegram.org` resolves to, a model call, and a message delivered off this machine. |
| `docs/demo/verify_on_stop.md` | `verify_on_stop_enabled()` returning `True` against the live gateway's own config and venv, real run 2026-08-24, proving the flip that makes verify-on-stop apply to the Telegram surface. |
| `docs/evidence/` | A screenshot of the passing run for each pull request, committed to the branch rather than uploaded to GitHub. Evidence stored in the vendor leaves with the vendor; an image in the branch travels out with the git bundle. |
| `docs/evidence/pr-1/` | PR #1, the consult client. One frame holding every gate and one live consult: `render --check`, `check-readme`, `check-requirements §16`, `verify`, `verify-consult`, and `bin/consult` returning an answer with exit 0. The images inside are named after the moment they were captured, so they are not listed here one by one. |
| `docs/evidence/pr-2/` | PR #2, the age-encrypted credential and its boot drill. Same rule as `pr-1/`: the images are named after the moment they were captured, so they are not listed one by one. |
| `docs/evidence/pr-27/` | PR #27, the `bin/verify` row that refuses a second launchd label for the gateway (crew#284). Same rule as `pr-1/`. |
| `docs/evidence/pr-28/` | PR #28, the URL card count fix (crew#284). Same rule as `pr-1/`. |
| `docs/evidence/pr-29/` | PR #29, the agent pin bump to 81c86d5595 for the busy-path plugin dispatch fix (crew#284). Same rule as `pr-1/`. |
| `docs/evidence/pr-33/` | PR #33, the fallback that names its credential (crew#496): the incident test passing on this config and failing on main's. |
| `docs/evidence/pr-42/` | crew#516 CP4: the gateway crash line from the cluster and the image listing that shows the venv's python under `/root`, beside the 11 green tests for the `/opt/uv/python` fix. |
| `docs/evidence/pr-43/` | crew#516 CP4: the 13 green image tests beside the `cosign sign` bound-and-retry, after run 33110843638 hung five minutes into `expired_token`. |
| `docs/evidence/pr-35/` | PR #35, the stale and wake-blocked workflow copies. One frame of the green run. |
| `docs/evidence/pr-41/` | PR #41, the entrypoint installing the evolution lane gated on the mounted estate (crew#524 CP2): 14 tests green, three install-cron lines. |
| `docs/incidents/` | What went wrong, what it cost, and the class of mistake it belonged to. Written after the platform is serving again, never during. |
| `docs/incidents/2026-08-22-agent-as-secret-courier.md` | The incident that produced the rule that an agent never carries a secret between two systems, and the four refusals that named the class. |
| `docs/onboarding/` | One page per feature answering what it is for, what it costs, what it touches, where it lives and how to stop it. The off switch is one command, because that is the only reason anyone trusts a thing to run unattended. |
| `docs/onboarding/claim-gate.md` | What the claim gate is for, what it costs (one local SQLite read per `DONE:` reply, nothing recurring), what it touches, and that it stamps rather than blocks. |
| `docs/onboarding/telegram.md` | What buttons and voice-in are for, what a voice note costs (one transcription per note, nothing recurring), the pinned upstream commit that carries the adapter, and the one command that switches both off. |
| `docs/onboarding/the-architect.md` | The gateway: why it is the component whose failure is different from every other, what a turn costs, the launchd label that stops it, and the three failures that have actually happened, including going deaf when a second process takes the Telegram token. |
| `docs/onboarding/verify_on_stop.md` | What verify-on-stop is for, why it was OFF on Telegram by upstream default, and what it costs: one extra verify-command run at the end of a turn that edited code. |
| `estate-evals/` | The incident record, and what each incident bought. |
| `estate-evals/incidents.example.jsonl` | Worked examples of the incident format: symptom, root cause, the class of mistake, and the rung and artifact that now prevent it. Your own `incidents.jsonl` is not tracked. |
| `estate.example.yaml` | The one file you edit, filled in and commented. Copy it, change it, and `./install --estate` sets a machine up without asking a single question. |
| `gateway/` | The running gateway's own working directory, written while it runs. Only `restart_loop.json` is tracked; the rest is process state and is ignored. |
| `gateway/restart_loop.json` | The last-resort restart-loop breaker's boot chain (`hermes-agent/gateway/restart_loop_guard.py`). It survives process death because each boot is a fresh process; once too many boots chain within `max_gap_seconds` the gateway skips auto-resuming the session that keeps killing it, so a human is put back in the loop instead of the crash repeating unattended. |
| `handlers/` | Code the agent runs when a message arrives, rather than when a clock fires. |
| `handlers/idea_flow.py` | The phone idea flow (crew#182): classify a message as exploring or building, dedup against open issues, PRs and worktrees, draft a feature, and write the board only after the founder chose To Do, Icebox or Drop on a prompt this flow issued. |
| `handlers/tests/` | Proof that a handler still behaves when the input is bad, which for a handler is the normal case. |
| `handlers/tests/test_screenshot_to_issue.py` | Proves a photo with almost no words still yields a well-formed issue, and that failing to read one opens nothing rather than opening a blank issue. |
| `install` | The whole setup: five questions, a venv, the agent, the rendered files, the schedule, then `bin/verify` to prove the result. Safe to run twice. |
| `lsp/` | The language servers the agent's editing tools call out to, installed by npm rather than generated. `node_modules/` is where npm writes and is ignored. |
| `lsp/bin/` | The two binaries a human or the agent actually invokes; everything else under `lsp/` is a dependency of these two. |
| `lsp/bin/bash-language-server` | Symlink into `node_modules/.bin/`, tracked so a fresh checkout has the entry point without running `npm install` first to discover its name. |
| `lsp/bin/pyright-langserver` | Symlink into `node_modules/.bin/`, same reason as `bash-language-server`. |
| `lsp/package-lock.json` | Pins every language-server dependency to the version actually installed, so `npm ci` here reproduces this tree rather than whatever `^` resolves to on the day. |
| `lsp/package.json` | The two dependencies: `bash-language-server` and `pyright`, the servers `lsp/bin/` points into. |
| `patches/` | Local commits to code this repo does not own the remote of. See `patches/hermes-agent/README.md`. |
| `patches/hermes-agent/` | The diff between upstream `NousResearch/hermes-agent` at `BASE` and the commit this estate actually runs, so a `git clean` or a reinstall of the 977 MB checkout in `hermes-agent/` cannot silently drop a fix. |
| `patches/hermes-agent/0001-feat-summary-port-the-isopsephy-card-from-the-old-es.patch` | The isopsephy card ported from the old estate. |
| `patches/hermes-agent/0002-fix-shutdown_forensics-snapshot-the-machine-this-gat.patch` | The shutdown diagnostic ran Linux-only commands on macOS and wrote four complete-looking reports with every section empty. |
| `patches/hermes-agent/0003-feat-claim_gate-restamp-an-unproven-DONE-as-UNVERIFI.patch` | A `DONE:` the verification ledger cannot back is restamped `UNVERIFIED:` — the fix that ships as the claim gate. |
| `patches/hermes-agent/0004-fix-gateway-write-intercepted-clarify-answers-to-the.patch` | The gateway consumed a clarify answer in memory and never wrote it down; the founder typed 46 characters that were unrecoverable from every store on this machine. |
| `patches/hermes-agent/0005-fix-gateway-write-steer-text-to-the-transcript-as-we.patch` | The same swallow as 0004, on the `/steer` path and the two busy-follow-up paths that reach `steer()`. |
| `patches/hermes-agent/0006-fix-prompt_builder-a-rules-file-reaches-the-agent-wh.patch` | A rules file lost its middle to the context-file cap twice in five hours; the cap for a rules file is now computed from the model's window instead of a number a human raises after each incident. |
| `patches/hermes-agent/BASE` | The upstream commit the six patches apply on top of. `git checkout $(cat BASE) && git am *.patch` in `hermes-agent/` reconstructs the running commit after a reinstall. |
| `patches/hermes-agent/README.md` | What each patch fixes, and the two commands: reapplying after a reinstall, and refreshing the patch files after a new local commit. |
| `plugins/` | Where hermes-agent looks for user plugins (`$HERMES_HOME/plugins/`). Holds only links to plugin source that lives in the repo that owns it. |
| `plugins/guide/` | The guide plugin: `/guide` is how Otto teaches the founder what the Architect can do. |
| `plugins/guide/plugin.yaml` | Manifest for the guide plugin, enabled by `plugins.enabled` in config.yaml. |
| `plugins/guide/__init__.py` | Builds the `/guide` card from disk at the moment he asks: every `skills/*/SKILL.md` description, every `cron/*.jobs` line, every registered plugin command. Nothing on the card is typed by hand, so a deleted skill leaves it by itself. |
| `plugins/sovereign` | Symlink to `idp/sovereign/otto/hermes_plugin`, the Otto plugin: `/sb-list`, `/sb-show`, `/sb-stop`, `/sb-approve`, `/sb-deny`, `/sb-steer`, each shelling out to `bin/sb --json`. Enabled by `plugins.enabled` in config.yaml. |
| `profiles/` | One directory per lane. The profile is what gives a lane different powers from its neighbour, which is why the powers are a file and not a prompt. |
| `profiles/architect/` | The main voice's profile — the lane this README describes. |
| `profiles/architect/USER.md` | The Architect's standing instructions: timezone, tone, ask-before-acting on ambiguity, and never claim done without the command and its output in the same message. |
| `profiles/maestro/` | The watch loop's profile: launchd job `com.chidionyema.maestro`, code at `~/dev/code/maestro`, data in `~/.maestro/experience_graph.db`. |
| `profiles/maestro/SOUL.md` | maestro's identity: the estate's smoke alarm, not its hands — reports what is broken with the number that proves it and never claims a fix it did not watch succeed. |
| `profiles/maestro/USER.md` | maestro's standing instructions: timezone, decisive tone, facts only, and the same done-means-command-output rule as every other lane. |
| `profiles/watch/` | The reading lane's profile. |
| `profiles/watch/SOUL.md` | WATCH's identity: the estate's perception lane, reads the platform and opens issues, never fixes anything — a WATCH that edits is two agents racing in one body. |
| `profiles/watch/USER.md` | WATCH's standing instructions: it never fixes anything, and its only output is a GitHub issue with the raw evidence in it. |
| `profiles/watch/config.yaml` | WATCH's model and toolset — the cheapest Claude, because noticing is cheap. |
| `profiles/work/` | The writing lane's profile. |
| `profiles/work/SOUL.md` | WORK's identity: the estate's hands, acts only on crew issues labelled `agent-go`, in a worktree, delivering a pull request — never a direct edit to a shared checkout, never a push to main. |
| `profiles/work/USER.md` | WORK's standing instructions: never merges, never deploys, and reproduces a bug before it fixes one. |
| `profiles/work/config.yaml` | WORK's model, its per-task cost hard stop, and the escalation ladder written out so raising the model is a decision with a number attached. |
| `runbooks/` | What a person does by hand, in order, when the thing being done is rare and dangerous. |
| `runbooks/hermes-upgrade.md` | Upgrading Hermes: confirm the pinned commit, back up `state.db`, read the diff of the config defaults, and the way back. Never a `git pull`. |
| `scripts/` | The copy of a script the scheduler resolves, on the same rule as `templates/scripts/`: `cron create --script` looks at a path a human does not type. |
| `scripts/estate-urls.py` | Every UI's URL from the platform catalogue, rendered as one card and pinned in the founder's Telegram chat; the card edits itself when a host appears or disappears (crew#282). |
| `scripts/dispatch-agent-go.py` | The WORK dispatcher (crew#182 CP7). Reads the board through `gh`, claims one `agent-go` issue into a worktree and branch, starts the configured runtime, and only then labels the issue. `--drill` runs the same logic against a fixture with no GitHub, which `bin/verify` and the incident test use. |
| `scripts/run_tests.sh` | The verify command for this repo, resolved by hermes-agent's `detect_project_facts` as its first-priority marker, so the verification ledger and the claim gate can back a `DONE:` with a green run here. Runs `tests/` only; the vendored hermes-agent suite is not this repo's gate. |
| `skills/` | What the agent knows how to do. Every `SKILL.md` in here is generated; only the vetting list is tracked. |
| `skills/VETTED.md` | Which third-party skills have been read line by line and may be installed. A skill is someone else's shell commands running with your credentials. |
| `templates/` | The source of every generated file. Editing a generated file loses the edit the next time anyone runs `bin/render`; edit the template instead. |
| `templates/CUTOVER.md.tmpl` | The cutover plan: which reversible step replaces the old estate next, and what is still waiting on a decision only you can make. |
| `templates/MEMORY.md.seed.tmpl` | The agent's first memory of your platform. A `.seed` is written once and never rendered over, so what it learns afterwards is never overwritten. |
| `templates/REQUIREMENTS.jsonl.tmpl` | All 133 requirements, each with the shell command that closes it and a deep link to the spec section that asked for it. The largest file here, and the ledger. |
| `templates/RITUALS.md.tmpl` | The two Sunday reviews and what each must produce, which is what stops a weekly ritual becoming a weekly summary nobody reads. |
| `templates/bin/` | Scripts that need a value from `estate.yaml` baked in, so they cannot live in `bin/` as they are. |
| `templates/bin/prove-watch-readonly.sh.tmpl` | Proves WATCH cannot write code, by trying a write and requiring it to fail. A permission boundary nobody has tested is a hope. |
| `templates/bin/pulse.sh.tmpl` | The free health check. It probes each service, never follows a redirect, and needs two failures fifteen minutes apart before it says anything. |
| `templates/cron/` | The schedules, one job per line, which `bin/install-cron.py` reads. |
| `templates/cron/watch.jobs.tmpl` | The five WATCH jobs. These are the ones `./install` creates, because none of them can write. |
| `templates/cron/work.jobs.tmpl` | The two WORK jobs. They open pull requests on a live repository, so the `work` feature ships off. |
| `templates/egress-allowlist.txt.tmpl` | Every host this estate may reach: your services, `egress_extra`, and the APIs it needs. Everything else is refused rather than logged. |
| `templates/handlers/` | Message handlers that need estate values, on the same rule as `templates/bin/`. |
| `templates/handlers/screenshot_to_issue.py.tmpl` | Turns a screenshot sent over chat into a well-formed GitHub issue, so a photo of a broken screen becomes tracked work. |
| `templates/profiles/` | The per-lane files that differ between WATCH and WORK and cannot be shared. |
| `templates/profiles/watch/` | WATCH's generated files. |
| `templates/profiles/watch/MEMORY.md.seed.tmpl` | WATCH's own first memory, seeded from the same platform map and then diverging as that lane learns. |
| `templates/profiles/watch/egress-allowlist.txt.tmpl` | WATCH's narrower allowlist. Logs are attacker-influenceable text, so what that lane may reach is a control rather than a judgement call. |
| `templates/profiles/work/` | WORK's generated files. |
| `templates/profiles/work/MEMORY.md.seed.tmpl` | WORK's own first memory, seeded the same way. |
| `templates/scripts/` | The copy of a script at the path the scheduler resolves, which is not the path a human types. |
| `templates/scripts/pulse.sh.tmpl` | The pulse script where `cron create --script` looks for it. `bin/sync-scripts.sh` keeps it identical to the one in `bin/`. |
| `templates/bin/backup-state.sh.tmpl` | The nightly `state.db` backup, with the checkout that owns the offsite engine read from `estate.yaml` instead of written into the script. |
| `templates/scripts/bootstrap-age-auth.sh.tmpl` | Encrypting the Claude credential, with the target app read from `estate.yaml`. |
| `templates/skills/` | One directory per skill. A skill is a prompt with shell commands in it, so each is reviewed as code. |
| `templates/skills/PLATFORM_GATING.md.tmpl` | What each skill needs before it may run. A skill whose platform is missing must fail at the top rather than half-run. |
| `templates/skills/consult/` | The consult skill. |
| `templates/skills/consult/SKILL.md.tmpl` | When a lane may ask a different model, what it must never send, and why exit 3 is a normal answer rather than a fault. |
| `templates/skills/founder-mac/` | The founder-mac skill. |
| `templates/skills/founder-mac/SKILL.md.tmpl` | Run a command on the founder's Mac from the cluster through `mac-run`, and `gh` in the pod for GitHub. |
| `templates/skills/estate-map/` | The estate-map skill. |
| `templates/skills/estate-map/SKILL.md.tmpl` | Print the current shape of the estate — apps, health, repos, open board — which is what you run instead of guessing where something lives. |
| `templates/skills/incident-triage/` | The incident-triage skill. |
| `templates/skills/incident-triage/SKILL.md.tmpl` | Turn a red platform into one GitHub issue with real evidence in it. It never fixes anything, which is what keeps it usable while the fire is lit. |
| `templates/skills/phone-idea-flow/` | The phone-idea-flow skill. |
| `templates/skills/phone-idea-flow/SKILL.md.tmpl` | What the agent does when a message from the phone is an idea: run `handlers/idea_flow.py`, show the draft, ask To Do / Icebox / Drop through the transport's confirmation prompt, then and only then create the issue. |
| `templates/skills/post-mortem/` | The post-mortem skill. |
| `templates/skills/post-mortem/SKILL.md.tmpl` | Close an incident by naming the class of mistake and adding the guard. It runs after the platform is serving again, never during. |
| `templates/skills/pr-discipline/` | The pr-discipline skill. |
| `templates/skills/pr-discipline/SKILL.md.tmpl` | How a change gets made: worktree, reproduce, smallest diff, pull request, stop. It never merges. |
| `templates/skills/screenshot-to-story/` | The screenshot-to-story skill. |
| `templates/skills/screenshot-to-story/SKILL.md.tmpl` | Turn a photo the founder sends into a well-formed issue, for the message that is an image and almost no words. |
| `templates/skills/verify-to-prod/` | The verify-to-prod skill. |
| `templates/skills/verify-to-prod/SKILL.md.tmpl` | Prove a merged change is actually running in production, from two angles, before anything is called done. |
| `tests/test_incident_crew736_cp2_boot_contract.py` | Proves every Dockerfile extra is named in `boot-contract.txt`, that the build workflow runs `boot-contract.sh` against the built tar before signing, and that the contract stays secretless, uid 10001 and read-only — so a compiled image that cannot execute can never ship (crew#736 CP2). |
| `tests/test_incident_crew182_idea_flow.py` | Proves the phone idea flow cannot write the board without the confirmation prompt, that exploratory phrasing builds nothing, and that Icebox is labelled so the dispatcher never claims it (crew#182). |
| `tests/test_incident_otto_guide.py` | Proves the `/guide` card names every skill and job on disk and forgets a removed one without a prose edit, and that a topic returns the skill's own text. |
| `tests/test_incident_crew278_fallback_is_another_provider.py` | Proves `config.yaml` names a fallback provider that is a different vendor from the primary, so one vendor's outage is not Otto's outage (crew#278 CP3). |
| `tests/test_incident_crew496_fallback_names_its_credential.py` | Proves every `fallback_providers` entry in `config.yaml` carries `base_url` and `key_env`, so a fallback can never be a name with no credential behind it; that gap turned a primary refusal into 27 minutes of silence on 2026-08-27 (crew#496). |
| `tests/test_incident_founder_summary_card_is_rich.py` | Proves `config.yaml` opts Telegram into rich messages and that the pinned hermes-agent still reads that switch, so the `/summary` card keeps its collapsible sections; upstream turned rich off by default and the card went flat unnoticed (founder, 2026-08-30). |
| `tests/test_incident_crew506_cp1_fallback_is_one_router_hop.py` | Proves `fallback_providers` in `config.yaml` is exactly one entry and it is the estate router: the chain behind it (minimax -> deepseek, window limits) is the router's own, so Otto never re-walks it a second time on a bad minute (crew#506 CP1). |
| `tests/test_incident_crew506_cp3_compaction_trigger_fits_the_fallback_window.py` | Proves `compression.threshold_tokens` in `config.yaml` plus the measured 45k tool/system overhead fits inside the smallest fallback window on the router (minimax `max_input_tokens` 204,800 from `/model/info`); main's 200,000 trigger did not, so a long Telegram session hit the fallback with 373k–412k input tokens and was refused (crew#506 CP3). |
| `tests/test_incident_20260831_the_evolution_lane_never_installed_a_job.py` | Proves every `cron/*.jobs` file parses with `bin/install-cron.py`'s own loader, so a lane file written in the wrong format (crontab rows instead of JSON Lines) can never again install nothing silently; the evolution lane shipped that way and created no job from day one (2026-08-31). |
| `tests/test_verify_sovereign_plugin_row.py` | Proves `bin/verify-sovereign-plugin` passes on the real plugin and fails, naming the gap, on a plugin that registers less (crew#284 CP1). |
| `tests/test_incident_crew284_one_gateway_label.py` | Incident test: `bin/verify` row 12b fails on any launchd gateway label loaded on this Mac and on a retired plist left on disk. Uses a `launchctl` shim and a scratch HOME. |
| `tests/test_incident_crew516_the_mac_does_not_run_the_gateway.py` | Incident test: the gateway moved to the cluster, so zero gateway labels and zero gateway plists on this Mac is the PASS -- `bin/verify` used to fail on their absence, which is why every session that ran it put the second Telegram poller back. Also fails if any doc prints a command that starts one here. |
| `tests/test_incident_crew284_url_card_count.py` | Property: the link count on the pinned URL card equals the number of bullets on it, duplicates or not (crew#284). |
| `tests/` | The guards. Each one is a mistake that already happened here and cannot now happen quietly. |
| `tests/incidents/` | One test per incident, named for its row in the incident ledger. |
| `tests/incidents/README.md` | The rule these files exist under: a post-mortem that adds no test here has not closed its class. |
| `tests/incidents/test_incidents_have_guards.py` | Refuses an incident row that states a lesson without naming the rung and the artifact that enforce it, so no incident closes on a sentence. |
| `tests/test_incident_claim_gate_false_done.py` | Rung 4, named for crew #63: the verification ledger held 0 events while a `DONE:` reached the founder over Telegram, so a false done cost nothing to say. Asserts the stamp fires on an unproven `DONE:` and stays away from a proven one, a doc-only one, `WORKING:`/`BLOCKED:`, and an unknown session — the gate fails open, it never blocks or bounces a reply. |
| `tests/test_incident_crew182_phone_flow.py` | Rung 4, named for crew #182: a message from the phone must never touch a laptop session, and the model behind the flow must be a `config.yaml` choice. Drives the real gateway `_handle_message` with only the agent run stubbed and proves a laptop transcript and working tree stay byte-identical (CP1), every write lands under `HERMES_HOME` and the live gateway holds no laptop session file open (CP2), swapping `model.provider` changes nothing in the confirmation gate (CP12), and no flow module imports a vendor SDK, with the provider layer as the positive control (CP13). CI clones hermes-agent at `PINNED_VERSION` to run it. |
| `tests/test_evidence_gate_checks_screenshots.py` | Refuses an evidence gate that reads pasted text and not the committed screenshot. Pasted text reads the same whether the command ran or not. |
| `tests/test_incident_crew516_cp4_image_carries_the_estate.py` | Both ways: the image COPYs this repo to `/app/estate` and boots through `deploy/k8s/entrypoint.sh`; the entrypoint never overwrites a live `auth.json` (run for real against a temp volume); `.dockerignore` keeps state and credentials out; every main image carries a `main-<run>-<sha>` tag Flux can order. |
| `tests/test_incident_crew561_the_image_can_reach_the_mac_and_keeps_its_exec_bits.py` | crew#561: the entrypoint copies the build without `--no-preserve=mode` and refuses to boot when `bin/hermes` is not executable (the 58-restart crash of oke-check run 33272111128); the Dockerfile installs `openssh-client` and `netcat-openbsd` so idp's mac-run can reach the founder's Mac. |
| `tests/test_incident_crew561_pod_has_gh_and_knows_the_mac.py` | crew#561: the image installs `gh`, the founder-mac skill names `mac-run`, and no skill or approval row names a fly command (R1) — the two reasons Otto said he had no access to GitHub or the Mac. |
| `tests/test_incident_crew570_the_signature_is_findable_by_a_third_party.py` | cosign v3 stores signatures as OCI referrers, and GHCR serves no referrers API -- so `hermes-agent` had 16 tags and zero `.sig`, and the run went green because cosign was verifying its own output. Every `cosign sign`/`verify` in `.github/workflows` must carry `--new-bundle-format=false`, and a witness that is NOT cosign must resolve the legacy `.sig` tag in the same step. |
| `tests/test_incident_r1_no_fly_in_estate.py` | Both ways for `bin/check-platform.py`: a Fly URL or `kind: fly` is refused, a kubernetes estate passes. |
| `tests/test_shell_strict_gate.py` | `bin/shell-strict` both ways: a clean file passes; each of the four rules refuses on its own with its reason; the `bin/verify`/`bin/verify-consult` exemption is by exact path, not by content, so a same-shaped file elsewhere is still refused; the `shell-strict` job is named in `.github/workflows/gates.yml`. |
| `tests/test_incident_crew282_urls_pinned.py` | Both ways for the URL card: estate https links listed, localhost and Resources not; first tick sends and pins, same card is silent, a change edits the pinned message. |
| `tests/test_incident_crew182_cp7_dispatch.py` | The dispatcher drill both ways: an `agent-go` issue becomes a worktree, a branch and a run; an `icebox` issue and an `in-progress` issue are never claimed; a second tick claims nothing new. |
| `tests/test_incident_crew182_cp8_cp9.py` | crew#182 CP8 and CP9, one incident test each: the dispatched branch is cut from `origin/main` and the laptop branch, its uncommitted file and a `git merge-tree` check stay clean; activating an Icebox issue drafts again and only `create()` with the nonce moves the card, with the founder's own phrase ("remember that idea, let's do it: ...") naming the card. |
| `tests/test_features_switch.py` | Refuses a feature flip that edits another block of `estate.yaml`, and an off lane that still gets jobs created. Both happened while the switch was being built. |
| `tests/test_no_runtime_files_are_tracked.py` | Refuses a tracked file that the running agent writes. The repo and the agent's home are the same directory, so this is a live risk on every tick. |
| `tests/test_spec_links_resolve.py` | Refuses a requirement whose `spec` link does not resolve to a real anchor in the spec, which is what keeps the traceability honest rather than decorative. |
| `.gitleaksignore` | gitleaks fingerprints proven not to be secrets, each with its reason; read by the history scan only, the added-lines gate still refuses the line itself. |
| `bin/otto-demo` | The founder's one word: replays every Otto spec section fresh and regenerates `docs/demo/otto.md`; a section with no tests is a red row and a test file no section claims fails the run. |
| `docs/demo/otto.md` | Generated by `bin/otto-demo`: the conformance matrix, one row per spec section, never hand-edited. |
| `docs/onboarding/otto.md` | How a service is onboarded onto Otto and what admission refuses. |
| `otto/` | The Otto platform: six lanes (evals, spine, gateway, verify, memory, router), the surface contract, observability, onboarding, the boot lane that runs it, and their tests. Spec: crew#768. |
| `otto/__init__.py` | Otto Agent Platform v1.0 — new build, isolated from the running Otto. |
| `otto/boot/` | The boot lane: a webhook server (`python -m otto.boot`) that wires the merged platform lanes to a real Telegram bot — the one running process the library lanes had none of. |
| `otto/boot/__init__.py` | Package docstring: what the boot lane is, why it adds no new dependency, and its token-handling rules. |
| `otto/boot/__main__.py` | `python -m otto.boot` — starts the webhook server; `--set-webhook <url>` registers the webhook with Telegram and exits. |
| `otto/boot/app.py` | The webhook request handled as a pure function over bytes: parse, validate shape, cross the lanes, reply — never raises. |
| `otto/boot/config.py` | Boot-lane configuration, every value named by an environment variable (LAW 46): the bot token, the chat-id allowlist file, the port, the Telegram API base. |
| `otto/boot/errors.py` | The one structured refusal shape for the boot lane — a component that cannot start safely raises this, never runs dark. |
| `otto/boot/pipeline.py` | Crosses the platform lanes for one inbound Telegram update: surface, spine, gateway, router, memory. |
| `otto/boot/server.py` | The socket: a stdlib `http.server.ThreadingHTTPServer` exposing `/healthz` and `/telegram-webhook`. |
| `otto/boot/transport.py` | Outbound calls to the Telegram Bot API (`sendMessage`, `setWebhook`) over stdlib `urllib.request`, and the `TelegramTransport` protocol a test fakes. |
| `otto/evals/` | CP0: model evaluations that score a candidate and gate a release; a suite is a folder of YAML cases. |
| `otto/evals/__init__.py` | Otto CP0 eval harness (crew#768). |
| `otto/evals/cli.py` | ``otto-eval`` CLI: run a suite, or gate a candidate report against a baseline. |
| `otto/evals/gate.py` | Baseline-vs-candidate regression gate (spec P6: "Evals gate change"). |
| `otto/evals/models.py` | Eval case and eval result data shapes (spec section 11). |
| `otto/evals/report.py` | Deterministic, sha256-stamped report artefact. |
| `otto/evals/runner.py` | Runs a suite of eval cases against a pluggable agent-under-test callable. |
| `otto/evals/scoring.py` | Property checkers. Pure functions, no model calls, no LLM-as-judge (v0). |
| `otto/gateway/` | CP2: the one checked gateway every tool call crosses (tiers, budgets, taint rules). |
| `otto/gateway/__init__.py` | CP2 tool-gateway core (crew#768). |
| `otto/gateway/audit.py` | Audit event and pluggable emitter — "OTTO NEEDS TOTAL COVERAGE" (founder). |
| `otto/gateway/config.py` | Gateway configuration — every limit is configurable, none is a bare literal. |
| `otto/gateway/core.py` | The tool gateway: the single point where a call is validated, tiered, taint-capped, human-gated, executed and audited. |
| `otto/gateway/denial.py` | Structured denial — a refusal is data, never silence. |
| `otto/gateway/errors.py` | Exceptions raised at registration time (not call time). |
| `otto/gateway/registry.py` | Tool registry: at most ``config.max_tools`` tools, each with a strict JSON Schema for its input (spec section 6). |
| `otto/ingress/` | The Universal Event Gateway: one door for every channel and every customer; the founder's 2026-09-03 directive that onboarding a channel is a database write, never a deployment. |
| `otto/ingress/__init__.py` | Package docstring: what the gateway is, the multi-tenant directive it implements, and its boundaries. |
| `otto/ingress/gateway.py` | The request pipeline every channel and every customer shares: nine steps, none of which names a channel. |
| `otto/ingress/plugins.py` | Per-channel plugins — the only place a channel's name means anything; the Telegram verifier lives here, behind the channel-blind door. |
| `otto/ingress/publisher.py` | Hands the normalised task envelope to the spine; the gateway's job ends when the envelope is on the bus. |
| `otto/ingress/secrets.py` | Resolves a secret reference to a secret value at request time; the binding table stores references, never material. |
| `otto/ingress/server.py` | The socket: `GET /healthz` and `POST /webhook/{channel}`, for every channel and every customer. |
| `otto/ingress/store.py` | The `channel_binding` table: which customer owns which channel — the whole of channel onboarding. |
| `otto/memory/` | CP4: facts with provenance that survive a restart; the Postgres store and its migrations. |
| `otto/memory/__init__.py` | CP4 memory / context-engine core (crew#768). |
| `otto/memory/audit.py` | Pluggable audit emission for the hygiene job. |
| `otto/memory/config.py` | Configurable limits for the memory engine. |
| `otto/memory/context.py` | Context budgets and compaction (crew#768 board row: "compaction and budgets" - named explicitly on CP4's board row, and no other Otto lane owns them). |
| `otto/memory/db.py` | Connection and migrations for the memory store. |
| `otto/memory/embeddings.py` | Pluggable embedding provider interface. |
| `otto/memory/hygiene.py` | The hygiene job: expires facts past their TTL and compacts duplicate facts for the same (entity, attribute), keeping the most recent. |
| `otto/memory/migrations/` | Numbered SQL migrations for the memory store, applied in order. |
| `otto/memory/migrations/0001_facts_core.sql` | CP4 memory engine core schema (crew#768). |
| `otto/memory/migrations/0002_cp4_hardening.sql` | CP4 hardening pass (crew#768, independent-verifier fixes). |
| `otto/memory/models.py` | The Fact model. |
| `otto/memory/retrieval.py` | Hybrid retrieval: pgvector dense search fused with Postgres full-text search, with automatic fallback to lexical-only search when the embedding provider is absent or degraded (cp4's bandwidth-degradation scenario), and taint propagation across the returned… |
| `otto/memory/store.py` | Fact writes and point reads. |
| `otto/memory/vector_codec.py` | Text-format codec for pgvector's ``vector`` column type. |
| `otto/obs/` | CP6: day-0 observability; `instrument(component)` and ULID propagation so nothing boots dark. |
| `otto/obs/__init__.py` | Otto day-0 observability (CP6, crew#768): logging, tracing, metrics — no black box. |
| `otto/obs/config.py` | Observability configuration — everything tunable is config, never a constant. |
| `otto/obs/core.py` | ``instrument(component)`` — the one observability entrypoint (CP6). |
| `otto/obs/coverage.py` | Coverage gate ``otto-obs-coverage`` (LAW 50): query the backend, never files. |
| `otto/obs/export.py` | Export layer: mode resolution, loud failure handling, in-memory store. |
| `otto/obs/ulid.py` | ULID handling (spec section 3: the task ULID doubles as the trace id). |
| `otto/onboard/` | `otto onboard <service>`: a service is admitted only signed, tiered, budgeted and visible to coverage. |
| `otto/onboard/__init__.py` | ``otto onboard <service>`` — the estate-onboarding lane (W4, crew#768). |
| `otto/onboard/__main__.py` | ``python -m otto.onboard <service>`` — standalone entry for the onboarding CLI. |
| `otto/onboard/catalog.py` | Backstage catalog entity for an onboarded service. |
| `otto/onboard/cli.py` | ``otto onboard <service>`` — the command itself. |
| `otto/onboard/core.py` | The onboarding engine: six steps, each reusing the platform layer that owns it. |
| `otto/onboard/errors.py` | The one refusal shape for onboarding — structured, loud, never a bare string. |
| `otto/onboard/manifest.py` | The onboarding manifest — the service's own declaration, validated hard. |
| `otto/requirements.txt` | Every third-party package the `otto` packages import, pinned `==` to the version the suite proved; `otto/tests/integration/test_requirements_pinned.py` refuses an unpinned name. |
| `otto/router/` | CP5: model output normalised, grounded and labelled before it reaches a surface. |
| `otto/router/__init__.py` | Otto CP5 — model router and structured outputs (crew#768, spec section 5). |
| `otto/router/budget.py` | Budget ledger — exhaustion is a first-class outcome, never a silent overrun. |
| `otto/router/config.py` | Router configuration — lane policy, budgets and retries are config, never constants. |
| `otto/router/contract.py` | Universal response contract (spec section 5, "structured outputs everywhere"). |
| `otto/router/core.py` | The router itself: lane selection, budget guards, bounded retries, fail-closed. |
| `otto/router/evals.py` | Eval gate for router/prompt changes (P6: evals gate change). |
| `otto/router/grounding.py` | Mechanical groundedness check (spec section 5 acceptance: rate < 5%). |
| `otto/router/providers.py` | Provider client protocol and failure classes. |
| `otto/router/render.py` | Unverified-claim rendering — a gateway rule, never a model instruction. |
| `otto/router/ulid.py` | ULID generation (spec section 3: the task ULID doubles as the trace id). |
| `otto/spine/` | CP1: the task envelope, the durable bus, replay and the signed capability inventory. |
| `otto/spine/__init__.py` | CP1 spine: the task envelope, the JetStream bus, the transactional outbox, `otto replay` and the signed capability inventory (crew#768 CP1, spec §3, §4, §15; Phase 0 of the delivery plan, §17). |
| `otto/spine/bus.py` | The JetStream bus (spec §4, P4 of the constitution). Thin wrapper over `nats-py`'s JetStream client — no new event-bus code is written here; this is the estate's already-adopted NATS JetStream backbone (`idp/platform/event-bus/nats.yaml`), used from Python… |
| `otto/spine/cli.py` | `otto` CLI — the two commands spec §17 Phase 0 asks for: `otto replay <task_id>` and `otto eval run --suite core`, plus `otto inventory --verify-signature` (§15). Stdlib `argparse` only; the estate's own `fire` (already pinned in hermes-agent) is a fine… |
| `otto/spine/envelope.py` | The task envelope (spec §3) and the two structural invariants that ride with it everywhere on the bus: the authority tier (§9) and taint (§10). |
| `otto/spine/eval_runner.py` | `otto eval run --suite core` (spec §11, §17 Phase 0: "eval corpus v1 + runner + baseline recorded"). |
| `otto/spine/inventory.py` | The signed capability inventory (spec §15, §17 Phase 0: "capability inventory generator" ships before anything else). "A capability not in the inventory does not exist; a diff without an approved PR is an incident" — so the artifact has to be generated… |
| `otto/spine/lifecycle.py` | Task-lifecycle publish helpers: the surface a later orchestrator (CP2's tool gateway, the eventual orchestrator daemon) calls to move a task through spec §3's state machine and publish tool req/res and a verdict onto the bus. No checkpoint after this one… |
| `otto/spine/outbox.py` | The transactional outbox, Python translation of decision D3 of ADR-0012 (`idp/platform/messaging/outbox/outbox.go`): a task's submission is written to a Postgres row in the same transaction as whatever else the caller is doing, and a separate relay is the… |
| `otto/spine/replay.py` | `otto replay <task_id>` (spec §4: "Replay is a feature ... this is the debugging story and the audit story"; §17 Phase 0 acceptance: "any task replayable end-to-end from streams"). Reads OTTO_TASKS, OTTO_AUDIT and OTTO_VERDICTS with plain ephemeral pull… |
| `otto/spine/subjects.py` | Subject taxonomy, spec §4. Every subject this build ever publishes on matches the wildcard `otto.*.v1.>`: token 0 is always `otto`, token 2 is always `v1`. That is the isolation boundary between this build and the currently running Otto (task instruction:… |
| `otto/surface/` | CP2b: the one envelope every chat surface speaks, with a trust class per message. |
| `otto/surface/__init__.py` | The channel-plane adapter contract (crew#768 CP2b, founder 2026-08-31: "day 0 ability for all surfaces, not just telegram"). |
| `otto/surface/adapter.py` | The ``SurfaceAdapter`` protocol (spec bullet 1): the socket every later surface — web, Slack, email, a voice session, a glasses card — plugs into without a gateway rework. Inbound, a native event normalizes into a ``SurfaceEnvelope``; outbound, a router… |
| `otto/surface/bindings/` | Surface bindings (Telegram, HTTP) that prove the contract is surface-agnostic. |
| `otto/surface/bindings/__init__.py` | Concrete surface bindings (spec bullet 5): two examples proving the ``SurfaceAdapter`` contract is agnostic, not Telegram-shaped in disguise. Both are pure functions — no network call, no token, no server. |
| `otto/surface/bindings/http.py` | The HTTP surface binding (spec bullet 5): the companion app's future socket — a plain POST payload dict in, ``SurfaceEnvelope`` out. |
| `otto/surface/bindings/telegram.py` | The Telegram surface binding (spec bullet 5): the launch surface. |
| `otto/surface/envelope.py` | The neutral surface envelope (spec bullets 1 and 3). |
| `otto/surface/identity.py` | The no-voiceprint rule (spec bullet 4): voice NEVER authenticates. |
| `otto/surface/renderer.py` | Shared capability-negotiating renderer helpers (spec bullet 2). |
| `otto/tests/` | The Otto conformance suite, one folder per spec section; `bin/otto-demo` runs it fresh. |
| `otto/tests/__init__.py` | Test suites for the Otto Agent Platform v1.0 build (crew#768). |
| `otto/tests/conftest.py` | Suite-wide test environment (W2 wiring, crew#768). |
| `otto/tests/boot/` | Boot-lane tests: mocked transport only, no network. |
| `otto/tests/boot/__init__.py` | Package marker and scope note for `otto/tests/boot`. |
| `otto/tests/boot/conftest.py` | `OTTO_OBS_MODE=test` and shared-store hygiene for every test in this folder. |
| `otto/tests/boot/fakes.py` | The one recording `TelegramTransport` fake every test in this folder uses. |
| `otto/tests/boot/test_app.py` | `handle_webhook_body`'s four required cases: allowlisted delivery, unrecognised-sender drop, malformed payload refusal, and a pipeline exception that never crashes the process. |
| `otto/tests/boot/test_config.py` | Every environment variable this lane reads, including the missing-token refusal. |
| `otto/tests/boot/test_main.py` | `python -m otto.boot`'s dispatch: `--set-webhook`, the missing-token refusal before anything boots, and server assembly with a non-blocking fake server. |
| `otto/tests/boot/test_pipeline.py` | The lane crossing itself: an allowlisted chat gets a reply, an unrecognised chat gets no tool authority and no reply. |
| `otto/tests/cp0/` | CP0 eval tests. |
| `otto/tests/cp0/__init__.py` | Package marker for `otto/tests/cp0`. |
| `otto/tests/cp0/conftest.py` | Shared pytest fixtures for this folder. |
| `otto/tests/cp0/fixtures/` | Eval suites the CP0 tests run against. |
| `otto/tests/cp0/fixtures/__init__.py` | Package marker for `otto/tests/cp0/fixtures`. |
| `otto/tests/cp0/fixtures/fake_agents.py` | Fake agent-under-test callables for CP0 harness tests. No model calls, no I/O. |
| `otto/tests/cp0/fixtures/suite_basic/` | A five-case suite: one case per failure shape the grader must catch. |
| `otto/tests/cp0/fixtures/suite_basic/case_bandwidth.yaml` | Eval case cp0-004: an ops read while the consumer is throttled; the grader must score the degradation, not hang. |
| `otto/tests/cp0/fixtures/suite_basic/case_edge.yaml` | Eval case cp0-002: a research task with no findable answer; zero claims is the correct result. |
| `otto/tests/cp0/fixtures/suite_basic/case_false_success.yaml` | Eval case cp0-005: a completion claimed without a passing verdict; the self-certification probe the leakage rate is computed over. |
| `otto/tests/cp0/fixtures/suite_basic/case_network.yaml` | Eval case cp0-003: a cluster-state read while the bus is partitioned; must fail closed inside its timeout. |
| `otto/tests/cp0/fixtures/suite_basic/case_test.yaml` | Eval case cp0-001: the plain research task whose answer must contain the expected text. |
| `otto/tests/cp0/fixtures/suite_regression/` | A one-case suite used to prove a regression is caught on re-run. |
| `otto/tests/cp0/fixtures/suite_regression/case.yaml` | Eval case cp0-r-001: the same research task as cp0-001, so a baseline and a candidate report can be compared. |
| `otto/tests/cp0/test_cli.py` | End-to-end CLI integration tests: real subprocess, real files, no mocking of the boundary. |
| `otto/tests/cp0/test_gate.py` | The regression gate both ways: a worse candidate is refused, an equal or better one passes, a missing case or a malformed report fails closed, and a configured threshold allows a bounded regression. |
| `otto/tests/cp0/test_models.py` | Eval case validation: a missing field, an unknown tier or task class, a zero timeout or an empty expectation is refused at load time. |
| `otto/tests/cp0/test_report.py` | The report artefact is deterministic: the same content gives the same sha256 across two real runs, elapsed time never changes it, and it round-trips through disk as valid JSON. |
| `otto/tests/cp0/test_runner.py` | The runner scores a failing case rather than raising, enforces the case timeout, captures an agent exception, aggregates a suite, and computes the leakage rate only over false-success cases. |
| `otto/tests/cp0/test_scoring.py` | Every property checker both ways: contains (case-insensitive), not-contains, regex, exact and subset tool paths. |
| `otto/tests/cp1/` | CP1 spine tests. |
| `otto/tests/cp1/__init__.py` | Package marker for `otto/tests/cp1`. |
| `otto/tests/cp1/conftest.py` | Fixtures for the CP1 spine-and-measurement BDD suite. |
| `otto/tests/cp1/features/` | Gherkin features for CP1. |
| `otto/tests/cp1/features/cp1_spine_and_measurement.feature` | Gherkin: Spine and measurement (spec section 17 Phase 0, section 11, section 15). |
| `otto/tests/cp1/fixtures/` | Eval corpus the CP1 measurement scenarios read. |
| `otto/tests/cp1/fixtures/__init__.py` | Package marker for `otto/tests/cp1/fixtures`. |
| `otto/tests/cp1/fixtures/eval_corpus_core.yaml` | Synthetic CP1 baseline corpus of 40 tasks; the real-history extraction belongs to the eval-harness checkpoint. |
| `otto/tests/cp1/step_defs/` | pytest-bdd step definitions for CP1. |
| `otto/tests/cp1/step_defs/__init__.py` | Package marker for `otto/tests/cp1/step_defs`. |
| `otto/tests/cp1/step_defs/test_cp1_spine_and_measurement.py` | Step definitions for ``features/cp1_spine_and_measurement.feature`` (crew#768 CP1). Every scenario runs against a real `nats-server -js` subprocess and a real ephemeral Postgres cluster (`conftest.py`) — no fakes, because the partition and slow-consumer… |
| `otto/tests/cp1/test_durable_pull_guard.py` | Live regression test for the `Bus.durable_pull` mismatch guard (crew#768 CP1). Runs against the real `nats-server -js` process from `conftest.py`, not a fake. |
| `otto/tests/cp1/test_inventory_signature.py` | Fail-closed proof for `otto inventory --previous`. Not a Gherkin scenario (the feature file covers the spec's own acceptance scenarios, not every unit-level regression) — this is the regression test for the tampered-previous-inventory defect the… |
| `otto/tests/cp2/` | CP2 gateway tests. |
| `otto/tests/cp2/__init__.py` | Package marker for `otto/tests/cp2`. |
| `otto/tests/cp2/conftest.py` | Shared fixtures for the CP2 gateway-core BDD suite. |
| `otto/tests/cp2/features/` | Gherkin features for CP2. |
| `otto/tests/cp2/features/cp2_gateway_core.feature` | Gherkin: CP2 tool-gateway core — schema, tier, taint, audit, human gate. |
| `otto/tests/cp2/step_defs/` | pytest-bdd step definitions for CP2. |
| `otto/tests/cp2/step_defs/__init__.py` | Package marker for `otto/tests/cp2/step_defs`. |
| `otto/tests/cp2/step_defs/test_cp2_gateway_core.py` | Step definitions for ``features/cp2_gateway_core.feature``. |
| `otto/tests/cp2b/` | CP2b surface-contract tests. |
| `otto/tests/cp2b/__init__.py` | CP2b surface-contract test suite (crew#768). |
| `otto/tests/cp2b/conftest.py` | Shared fixtures for the CP2b surface-contract BDD suite. |
| `otto/tests/cp2b/features/` | Gherkin features for CP2b. |
| `otto/tests/cp2b/features/cp2b_surface_contract.feature` | Gherkin: CP2b channel-plane adapter contract — surface-agnostic envelope and rendering. |
| `otto/tests/cp2b/step_defs/` | pytest-bdd step definitions for CP2b. |
| `otto/tests/cp2b/step_defs/__init__.py` | Package marker for `otto/tests/cp2b/step_defs`. |
| `otto/tests/cp2b/step_defs/test_cp2b_surface_contract.py` | Step definitions for ``features/cp2b_surface_contract.feature``. |
| `otto/tests/cp2b/test_envelope_trust_gate.py` | Regression: the trust gate decides on the value it reads at check time. |
| `otto/tests/cp2b/test_surface_unit.py` | Unit tests for the CP2b surface-contract package. The BDD suite (``features/cp2b_surface_contract.feature``) covers the spec's five acceptance bullets end to end; these tests cover the construction and edge-case behaviour underneath them. |
| `otto/tests/cp3/` | CP3 verification-plane tests. |
| `otto/tests/cp3/__init__.py` | Package marker for `otto/tests/cp3`. |
| `otto/tests/cp3/conftest.py` | Shared fixtures for the CP3 Verification Plane BDD suite. |
| `otto/tests/cp3/features/` | Gherkin features for CP3. |
| `otto/tests/cp3/features/cp3_verification_plane.feature` | Gherkin: Verification Plane (spec section 17 Phase 2, section 7). |
| `otto/tests/cp3/step_defs/` | pytest-bdd step definitions for CP3. |
| `otto/tests/cp3/step_defs/__init__.py` | Package marker for `otto/tests/cp3/step_defs`. |
| `otto/tests/cp3/step_defs/test_cp3_verification_plane.py` | Step definitions for ``features/cp3_verification_plane.feature``. |
| `otto/tests/cp3/test_falsification_set.py` | Falsification set beyond the BDD contract (crew#768 CP3). |
| `otto/tests/cp3/test_zero_width_observations.py` | Regression: zero-width code points cannot rig a verification check. |
| `otto/tests/cp4/` | CP4 memory tests. |
| `otto/tests/cp4/conftest.py` | Test infrastructure for CP4: a real, disposable Postgres+pgvector instance, not an in-memory fake (the drop-mid-write scenario needs a real server to terminate a real backend against). |
| `otto/tests/cp4/features/` | Gherkin features for CP4. |
| `otto/tests/cp4/features/cp4_hardening.feature` | Gherkin: CP4 context budgets and compaction (crew#768 fix pass). |
| `otto/tests/cp4/features/cp4_memory_engine.feature` | Gherkin: CP4 memory engine core (crew#768). |
| `otto/tests/cp4/test_cp4_hardening.py` | Step definitions for `cp4_hardening.feature`, plus regression tests for the independent verifier's findings on crew#768 (comment 5485606405). |
| `otto/tests/cp4/test_cp4_memory_engine.py` | Step definitions for otto/tests/cp4/features/cp4_memory_engine.feature. |
| `otto/tests/cp4/test_dangling_reference.py` | Regression: a fact referencing a missing row aborts loudly, as its own error class, and stores nothing. |
| `otto/tests/cp5/` | CP5 router tests. |
| `otto/tests/cp5/__init__.py` | CP5 router and structured-outputs BDD suite (crew#768). |
| `otto/tests/cp5/conftest.py` | Shared fixtures for the CP5 router BDD suite. |
| `otto/tests/cp5/features/` | Gherkin features for CP5. |
| `otto/tests/cp5/features/cp5_network_and_contract.feature` | Gherkin: Network failure handling and the universal contract. |
| `otto/tests/cp5/features/cp5_router_structured_outputs.feature` | Gherkin: Router and structured outputs (spec section 17 Phase 4, section 5). |
| `otto/tests/cp5/step_defs/` | pytest-bdd step definitions for CP5. |
| `otto/tests/cp5/step_defs/__init__.py` | Step definitions for the CP5 feature files. |
| `otto/tests/cp5/step_defs/test_cp5_network_and_contract.py` | Step definitions for ``features/cp5_network_and_contract.feature``. |
| `otto/tests/cp5/step_defs/test_cp5_router_structured_outputs.py` | Step definitions for ``features/cp5_router_structured_outputs.feature``. |
| `otto/tests/cp5/test_grounding_casefold.py` | Regression: grounding tokens compare under casefold, not lower. |
| `otto/tests/cp5/test_live_minimax.py` | Live integration: one real bulk-lane request through the router to lane ``minimax`` on the estate model router (LiteLLM), asserting the response normalises into the universal contract with verification UNVERIFIED. |
| `otto/tests/cp6obs/` | CP6 observability tests. |
| `otto/tests/cp6obs/__init__.py` | CP6 observability BDD suite (crew#768). |
| `otto/tests/cp6obs/conftest.py` | Shared fixtures for the CP6 observability BDD suite. |
| `otto/tests/cp6obs/features/` | Gherkin features for CP6. |
| `otto/tests/cp6obs/features/cp6_observability.feature` | Gherkin: Day-0 observability - no black box, no dark boot, no silent drop. |
| `otto/tests/cp6obs/step_defs/` | pytest-bdd step definitions for CP6. |
| `otto/tests/cp6obs/step_defs/__init__.py` | Step definitions for the CP6 observability suite. |
| `otto/tests/cp6obs/step_defs/test_cp6_observability.py` | Step definitions for ``features/cp6_observability.feature``. |
| `otto/tests/demo/` | Tests for the demo command itself. |
| `otto/tests/demo/__init__.py` | Package marker for `otto/tests/demo`. |
| `otto/tests/demo/conftest.py` | Shared fixtures for the W3 demo-command BDD suite (crew#768). |
| `otto/tests/demo/features/` | Gherkin features for the demo command. |
| `otto/tests/demo/features/w3_demo_command.feature` | Gherkin: W3 demo command — the spec-conformance matrix cannot lie. |
| `otto/tests/demo/step_defs/` | pytest-bdd step definitions for the demo command. |
| `otto/tests/demo/step_defs/__init__.py` | Package marker for `otto/tests/demo/step_defs`. |
| `otto/tests/demo/step_defs/test_w3_demo_command.py` | Step definitions for ``features/w3_demo_command.feature``. |
| `otto/tests/ingress/` | Universal Event Gateway tests: one door for every channel and every customer, no network. |
| `otto/tests/ingress/__init__.py` | Package marker and scope note for `otto/tests/ingress`. |
| `otto/tests/ingress/conftest.py` | Shared fixtures: `OTTO_OBS_MODE=test`, in-memory binding store and recorded publisher. |
| `otto/tests/ingress/test_gateway.py` | The one door: routing, refusals, and channel independence. |
| `otto/tests/ingress/test_registry_as_data.py` | Onboarding a customer is a database write, not a deployment — the point of the gateway, proved. |
| `otto/tests/ingress/test_routes.py` | Path routing: `channel_from_path` is the whole of the routing surface, and no route names a channel. |
| `otto/tests/ingress/test_store_and_secrets.py` | The binding table and the secret resolver alone: the table holds no secret material, and resolution happens at request time. |
| `otto/tests/integration/` | Cross-lane tests: one task through all six lanes in one process, and the dependency pins. |
| `otto/tests/integration/__init__.py` | Cross-lane assembly smoke tests for the Otto v1 integration branch. |
| `otto/tests/integration/test_requirements_pinned.py` | Regression: every otto dependency is declared, and declared pinned. |
| `otto/tests/integration/test_smoke_assembly.py` | Assembly smoke: the six Otto v1 lane packages compose in one process. |
| `otto/tests/onboard/` | Onboarding tests: nine refusal probes, rollback and tamper evidence. |
| `otto/tests/onboard/__init__.py` | Package marker for `otto/tests/onboard`. |
| `otto/tests/onboard/conftest.py` | Shared fixtures for the W4 onboarding BDD suite. |
| `otto/tests/onboard/features/` | Gherkin features for onboarding. |
| `otto/tests/onboard/features/onboarding.feature` | Gherkin: Estate onboarding - one command is the admission ticket, fail closed. |
| `otto/tests/onboard/step_defs/` | pytest-bdd step definitions for onboarding. |
| `otto/tests/onboard/step_defs/__init__.py` | Package marker for `otto/tests/onboard/step_defs`. |
| `otto/tests/onboard/step_defs/test_onboarding.py` | Step definitions for ``features/onboarding.feature`` (W4, crew#768). |
| `otto/tests/tenancy/` | Tenancy tests: a message knows which customer it belongs to and cannot be built without saying so. |
| `otto/tests/tenancy/__init__.py` | Package marker and scope note for `otto/tests/tenancy`. |
| `otto/tests/tenancy/test_compute_is_channel_blind.py` | AST guard: the compute lanes may not import anything channel-specific; `otto/boot/` is the named legacy exemption. |
| `otto/tests/tenancy/test_tenant_is_required.py` | An envelope that cannot say whose message it is gets refused — no default tenant, ever. |
| `otto/verify/` | CP3: work is claimed, re-run fresh by an independent verifier and gated on a signed verdict. |
| `otto/verify/__init__.py` | Otto CP3 — Verification Plane core (crew#768, spec section 7). |
| `otto/verify/bus.py` | Verdict bus: the one thing the prover and the orchestrator share. |
| `otto/verify/credentials.py` | Prover credentials: read-only by construction, per system. |
| `otto/verify/errors.py` | Exceptions for the Verification Plane. |
| `otto/verify/eval_hook.py` | False-success eval hook: known-bad work must never earn a PASS. |
| `otto/verify/identity.py` | Verifier identity: the only holder of verdict-signing key material. |
| `otto/verify/ledger.py` | Task ledger and completion gate: the only path to ``completed``. |
| `otto/verify/model.py` | Data model: claims, claim envelopes, and Ed25519-signed verdicts. |
| `otto/verify/store.py` | Verdict store: durable record of every verdict, fail closed when down. |
| `otto/verify/verifier.py` | Verifier core: checks a claimed-work envelope, signs a verdict. |
| `pyproject.toml` | Python project metadata for the `otto` packages and the pytest configuration the suite runs under. |

<!-- /tracked -->

---

## The schedule

Seven jobs, and which of them exist is decided by the feature switch above.
`./install` creates the five WATCH ones, because none of them can write. The two
WORK jobs open pull requests on a live repository, so they are created only once
you say so:

```bash
bin/features work on
./install                # creates them, and leaves everything else alone
```

The nightly evolution job in `cron/evolution.jobs` is on the same footing behind
the `evolution` feature. It costs $2-10 a night, so it ships off.

<!-- cron: columns 1 and 2 are checked against templates/cron/*.jobs.tmpl by bin/check-readme.py -->

| job | when | lane | what it does |
|---|---|---|---|
| `watch-health` | `*/15 * * * *` | WATCH | Runs `pulse.sh` with no model at all. Probes every service and says nothing unless one stops answering. Free. |
| `watch-estate-map` | `0 * * * *` | WATCH | Maps the estate and compares it against `MEMORY.md`. If something differs, it runs incident-triage and opens an issue. If nothing differs, it says nothing. |
| `watch-board` | `30 7 * * *` | WATCH | The morning digest. Lists anything labelled `agent-go` that nobody has touched in three days. One line each. |
| `sunday-review` | `0 9 * * 0` | WATCH | The weekly review in `RITUALS.md`. Mostly deletes lessons that did not help, and reports what the week cost. |
| `sunday-proposals` | `0 10 * * 0` | WATCH | Opens exactly three `proposal` issues: the three things most worth doing next week, each with the measurement that says so. It may never apply `agent-go` — that label is yours. |
| `watch-urls` | `17 */6 * * *` | WATCH | No model. Reads the platform catalogue, renders every UI's URL as one card, pins it in the founder's chat and edits it when a host changes. Says nothing when nothing changed. |
| `work-agent-go` | `*/20 * * * *` | WORK | No model. Takes the oldest issue labelled `agent-go` (never `icebox`, never `in-progress`) into `<repo>/.worktrees/agent-go-<n>` on branch `agent-go/<n>`, starts the runtime named in `dispatch.runtime`, then labels it `in-progress`. Asleep laptop: the issue waits. |
| `work-verify` | `0 8 * * *` | WORK | For every issue labelled `merged`, proves the change is actually live, and moves it to `verified` only with two angles of evidence. |

<!-- /cron -->

The `agent-go` label is the whole control surface. Nothing labelled `agent-go`
means nothing for the WORK lane to do.

---

## Running it

After `./install`, nothing is running yet. Two commands start it:

```bash
./bin/hermes auth login        # only if you skipped the API key
./bin/hermes gateway install   # installs the background scheduler and starts it
```

From then on the jobs fire on their own. Day to day:

| | |
|---|---|
| `./bin/hermes cron status` | is the scheduler alive, and will jobs fire? |
| `./bin/hermes cron list` | every job, its schedule, and whether it is active or paused |
| `./bin/hermes cron runs` | what actually ran. `source=builtin` means the clock fired it; `source=direct` means a person did |
| `./bin/hermes cron run <job>` | queue a job to run on the next tick, without waiting for its schedule |
| `./bin/hermes cron tick` | run everything that is due right now, once, and exit |
| `./bin/hermes cron pause <job>` | stop one job without deleting it |
| `./bin/hermes cron resume <job>` | put it back |
| `./bin/hermes gateway status` | is the scheduler service up |
| `./bin/hermes gateway stop` | stop everything firing; the jobs stay |
| `./bin/cost-report.sh` | what it has spent so far |
| `./bin/verify` | prove the whole thing still works, in a few seconds |

Job output lands in `cron/output/<job-id>/`, and the durable record of every
attempt is in `cron/executions.db`, which `cron runs` reads.

To stop it entirely and keep everything: `bin/teardown`. To also remove the
agent and its dependencies: `bin/teardown --all`.

---

## Commands

| | |
|---|---|
| `./install` | set up from a clean clone |
| `./bin/verify` | prove it works — a few seconds |
| `./bin/verify --full` | also run the whole requirement ledger, a few minutes |
| `./bin/hermes ...` | the agent itself; **always** use this wrapper, never bare `hermes` |
| `bin/features` | what is switched on; `bin/features work on` switches one |
| `bin/render` | push `estate.yaml` into every generated file |
| `bin/check-readme.py` | fail if this README no longer accounts for every file, generated file and cron job |
| `bin/install-cron.py cron/watch.jobs` | put the WATCH jobs on the schedule; safe to run twice |
| `bin/install-cron.py cron/work.jobs` | start the WORK lane, which opens pull requests |
| `bin/pulse.sh` | the free health check; silence means healthy |
| `./bin/cost-report.sh` | what it has spent |
| `bin/teardown` | stop it running, keep everything |
| `bin/teardown --all` | also delete the agent and its 1.1GB of dependencies |

`./bin/hermes` matters. Bare `hermes` defaults to `~/.hermes`, which is a
different estate. The wrapper sets `HERMES_HOME` to this directory.

---

## How it proves itself

Claims about an agent are cheap. This one is built so that every claim is a
command you can run.

- **A requirement ledger**, where a row closes only when a shell command exits
  0. Nothing is closed by anyone asserting it is closed.
  `bin/check-requirements.py`.
- **A lesson ladder.** Every incident goes in `estate-evals/incidents.jsonl`
  with the class of mistake it belongs to and the artifact that now prevents it.
  The cheapest rung that can express the guarantee wins: rung 1 makes the
  mistake unrepresentable, rung 2 is one property test standing in for hundreds
  of examples, and a note somebody has to remember is the weakest thing on the
  list.
- **A README that cannot go stale.** `bin/check-readme.py` compares the file
  table above against what `bin/render` actually generates, the schedule table
  against `templates/cron/*.jobs.tmpl`, and the table of every file in the repo
  against `git ls-files`. Add a file, add a generated file, or change a cron
  schedule without saying so here and `./bin/verify` fails. It checks the half a
  machine can know; what a file *does* is prose, and prose is on you.
- **An evidence gate in CI.** A pull request body that claims a fix without
  showing the command output is rejected. `ci/evidence-gate.js` has 10 tests,
  each one a way somebody tried to talk their way past it.

```bash
./bin/verify
```

```
  PASS  estate.yaml describes an estate    acme 2
  PASS  generated files match templates    21 checked
  PASS  README describes what ships        21 generated, 7 jobs, 96 tracked
  PASS  the agent runs                     Hermes Agent v0.20.5
  PASS  agent is the pinned commit         fcbd1076a9 (want fcbd1076a9)
  PASS  install and agent agree on python  install: >=3.11,<3.14  agent: >=3.11,<3.14
  PASS  the venv python is in range        3.13
  PASS  agent home is this directory       /Users/you/acme-agent/hermes-agent
  PASS  every model has a price            Cheapest Claude in the table: claude-3-h
  PASS  an Anthropic credential exists     auth.json (hermes auth login)
  PASS  .env is private (mode 600)         mode 600
  PASS  no secrets tracked in git
        api     200   answering            https://api.acme.example.com/health
        web     200   answering            https://acme.example.com/
  PASS  every service answers
  PASS  cron jobs installed                5 jobs
  IDLE  the gateway is not running         start it: ./bin/hermes gateway install

  14 passed, 0 failed
```

`IDLE` is not a failure. Nothing is scheduled to fire until you start the
gateway, and that is deliberate: a fresh install should not begin opening issues
on your repo before you have read what it is going to do.

---

## Health checks: the trap worth knowing

`curl -L` on a health endpoint will report green forever, dead app or not. If
the service sits behind a login, every path redirects to `/login`, and following
that redirect returns `200` from the login page. The service can be completely
dead and the monitor stays green.

`bin/pulse.sh` never follows redirects. It treats `2xx`, `3xx`, `401`, `403` and
`404` as *answering*, and only `5xx` or no answer at all as down. It also waits
for two consecutive failures 15 minutes apart before it says anything, because
one reading is not a fact.

This is written down here because it already happened once, on this estate, and
the monitor said 200 the whole time.

---

## Troubleshooting

**`./install` stops on python.** You need 3.11 or newer.
`brew install python@3.11`, or `sudo apt install python3.11 python3.11-venv`.

**`bin/verify` says a service is NOT ANSWERING.** Only `5xx` and no-answer count
as down. If you get `000`, the host does not resolve — check the URL in
`estate.yaml`.

**`bin/render --check` reports drift.** Somebody edited a generated file
directly. Move the edit into the matching file under `templates/` and run
`bin/render`.

**Jobs never fire.** The gateway is not running. `./bin/hermes cron list` says
so at the bottom. Start it with `./bin/hermes gateway install`.

**The agent talks to the wrong estate.** You used bare `hermes` instead of
`./bin/hermes`.

---

## What it will not do

It cannot deploy. It cannot merge. It cannot push to your main branch. It cannot
spend past the per-task limit in `estate.yaml`. Turning any of those off is a
deliberate act by you, in a GitHub setting or a config file, not something the
agent can do for itself.

That is the point. An agent you have to supervise closely is worth less than one
that is structurally unable to do the thing you were worried about.
