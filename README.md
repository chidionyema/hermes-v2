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
    url: https://acme-api.fly.dev/health      # what gets probed
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
| `deploy/fly/finish-cutover.sh` | Moves the Telegram gateway to the new app, proves it answers, and undoes itself if it does not. It handles no secrets, cannot read one, and has no flag that takes one. |
| `deploy/fly/fly.toml` | The app's own configuration: region, volume, and the process the machine runs. |
| `scripts/bootstrap-age-auth.sh` | Encrypts the Claude credential to a file the repo can carry. Run once, by a person, on a machine that already holds the credential. |
| `scripts/check-age-drill.sh` | Reads the drill's verdict off the volume from outside the app, and is the half that goes red. A log line in `fly logs` that nobody greps is not an alert. |
| `skills/PLATFORM_GATING.md` | What each skill needs before it may run. A skill whose platform is missing must fail at the top, not half-run. |
| `skills/consult/SKILL.md` | Ask a different model when you are stuck, and treat the answer as the weakest evidence you hold. Never acts on it unchecked. |
| `skills/estate-map/SKILL.md` | Print the current shape of the estate — apps, health, repos, open board. Run before guessing where anything lives. |
| `skills/incident-triage/SKILL.md` | Turn a red platform into one GitHub issue with real evidence in it. Never fixes anything. |
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
| `.dockerignore` | Keeps the Docker build context to source. Without it the context is 1.2 GB, almost none of which the image needs, and every deploy pays for it. |
| `.env.example` | Every credential the agent can use, each with an empty value and a line saying what it unlocks. `./install` copies it to `.env`, which is mode 600 and never tracked. |
| `.estate/` | Standing decisions about this repository that a machine has to be able to find. One file per decision, and deleting the file withdraws the decision. |
| `.estate/public-ciphertext-ok` | Records that committing age ciphertext to a public remote is deliberate, names what is committed and what opens it, and states the cost accepted: a public git object is permanent, so the credential inside is treated as rotatable. |
| `.gitignore` | What must never be committed: the generated files, the agent's runtime state, and `.env`. Most of its lines were added after a `git add -A` swept live state into a commit. |
| `PINNED_VERSION` | The Hermes tag and commit this estate is known to work on. `bin/verify` fails when the running agent is a different commit, so an upgrade cannot happen by accident. |
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
| `bin/verify` | The probe: one command that says whether the whole thing works, in a few seconds. `--full` also runs the requirement ledger. |
| `bin/verify-consult` | The same probe for the consult service (§16): daemon, loopback bind, 401 without a token, a live round-trip timed cold and warm, and the token never appearing in a log. Rows that cannot apply on this machine SKIP rather than fail, so it still runs off the founder's laptop. |
| `ci/` | The gates that run on a pull request. Each one refuses a mistake rather than reporting it. |
| `ci/EVIDENCE_GATE_PROOF.md` | The evidence gate refusing ten dishonest pull request bodies and passing three honest ones, with the output. A gate nobody has watched refuse anything is a claim. |
| `ci/evidence-gate.js` | Reads the pull request body and rejects a claim with no command output behind it. Placeholder blocks, and blocks that only repeat the claim, are rejected too. |
| `.github/` | The CI this repo runs on itself. Not to be confused with `ci/`, which holds the workflows this repo installs into another one. |
| `.github/workflows/` | One workflow. Everything a runner can honestly answer runs here on every pull request. |
| `.github/workflows/gates.yml` | The three gates that mean the same thing away from the founder's laptop: every template renders from `estate.example.yaml`, `check-readme.py`, and `verify-consult`. `bin/verify` is left out on purpose — it asks about the gateway, the venv and a credential, and a runner has none of them, so it would be red for being in the wrong place. This repo is public, so the minutes are free. |
| `ci/evidence-gate.yml` | The workflow that runs the gate: first the body check, then the check that a screenshot of the run is committed under `docs/evidence/pr-<n>/`. |
| `ci/static-gates.yml` | ruff, pyright strict, pip-audit and deptry as required checks on main. A red job here is not advisory. |
| `ci/tests/` | Tests of the gates themselves, because a gate that passes everything looks exactly like a gate that works. |
| `ci/tests/test_evidence_gate_gaming.py` | Thirteen pull request bodies, ten of them ways somebody tried to talk past the gate. It is what makes `EVIDENCE_GATE_PROOF.md` reproducible. |
| `config.yaml` | The agent's own settings: provider, default model, and `max_turns: 90` against an upstream default of 500, because a task needing more than 90 turns has gone wrong and should stop. |
| `cron/` | The scheduler's working directory. Only `.jobs` files belong to the repo; everything else in here is written while it runs and is ignored. |
| `cron/evolution.jobs` | The nightly skill-evolution schedule. Written by hand rather than rendered, because nothing in it varies from one estate to the next. |
| `deploy/` | Everything needed to run this estate somewhere other than the founder's laptop. Nothing in here runs locally. |
| `deploy/fly/` | The Fly target: image, entrypoint, config, and the two scripts that keep the fallback credential honest. |
| `deploy/fly/Dockerfile` | The image. It carries the code and no state; everything that survives a deploy is on the volume the entrypoint links in. |
| `deploy/fly/age-drill.sh` | Asks whether the age-encrypted fallback still opens and still holds the token in use. One implementation, two callers: once at boot and again whenever the live credential changes. |
| `deploy/fly/age-drill-watch.sh` | Re-runs that drill while the container is up. It is spawned by the entrypoint and nowhere else, because `AGE_PRIVATE_KEY` is a platform secret that reaches the entrypoint and is invisible to anything attached later with `fly ssh console`. |
| `deploy/fly/entrypoint.sh` | Links the writable names into the volume before starting the gateway. `HERMES_HOME` is the repo root, so a volume mounted over that root would hide the code; it mounts at `/data` instead. |
| `deploy/secrets/` | Ciphertext only. A cleartext credential has never been in this directory and the encryption is what makes it safe to track. |
| `deploy/secrets/claude-credentials.json.age` | The Claude credential encrypted to a key the platform holds, so a fresh container can decrypt it at boot without an agent ever carrying it. `scripts/bootstrap-age-auth.sh` writes it. |
| `docs/` | Written for a person to read, not for the machine to parse. |
| `docs/THE-ARCHITECT.md` | The spec. Every requirement row deep-links to the section it came from, so nothing is in this repo without a paragraph that asked for it. |
| `docs/claude-auth.md` | The whole credential chain, with links pinned to the exact upstream commit: where identity comes from, why the laptop's token cannot travel, and what to do when the fallback goes stale. |
| `docs/demo/` | One page per feature showing it running, with real pasted output under the command that produced it. Written for the founder, who did not build the thing and should not have to run it to find out whether it works. |
| `docs/demo/claim-gate.md` | The claim gate restamping a real `DONE:` as `UNVERIFIED:`, real run 2026-08-24: `stamp_unproven_done` against a throwaway ledger, pasted input and output side by side. |
| `docs/demo/the-architect.md` | The gateway doing all three of its jobs, from a real run: `bin/verify` at 17 passed 0 failed, the two live sockets to the address `api.telegram.org` resolves to, a model call, and a message delivered off this machine. |
| `docs/demo/verify_on_stop.md` | `verify_on_stop_enabled()` returning `True` against the live gateway's own config and venv, real run 2026-08-24, proving the flip that makes verify-on-stop apply to the Telegram surface. |
| `docs/evidence/` | A screenshot of the passing run for each pull request, committed to the branch rather than uploaded to GitHub. Evidence stored in the vendor leaves with the vendor; an image in the branch travels out with the git bundle. |
| `docs/evidence/pr-1/` | PR #1, the consult client. One frame holding every gate and one live consult: `render --check`, `check-readme`, `check-requirements §16`, `verify`, `verify-consult`, and `bin/consult` returning an answer with exit 0. The images inside are named after the moment they were captured, so they are not listed here one by one. |
| `docs/evidence/pr-2/` | PR #2, the age-encrypted credential and its boot drill. Same rule as `pr-1/`: the images are named after the moment they were captured, so they are not listed one by one. |
| `docs/incidents/` | What went wrong, what it cost, and the class of mistake it belonged to. Written after the platform is serving again, never during. |
| `docs/incidents/2026-08-22-agent-as-secret-courier.md` | The incident that produced the rule that an agent never carries a secret between two systems, and the four refusals that named the class. |
| `docs/onboarding/` | One page per feature answering what it is for, what it costs, what it touches, where it lives and how to stop it. The off switch is one command, because that is the only reason anyone trusts a thing to run unattended. |
| `docs/onboarding/claim-gate.md` | What the claim gate is for, what it costs (one local SQLite read per `DONE:` reply, nothing recurring), what it touches, and that it stamps rather than blocks. |
| `docs/onboarding/the-architect.md` | The gateway: why it is the component whose failure is different from every other, what a turn costs, the launchd label that stops it, and the three failures that have actually happened, including going deaf when a second process takes the Telegram token. |
| `docs/onboarding/verify_on_stop.md` | What verify-on-stop is for, why it was OFF on Telegram by upstream default, and what it costs: one extra verify-command run at the end of a turn that edited code. |
| `estate-evals/` | The incident record, and what each incident bought. |
| `estate-evals/incidents.example.jsonl` | Worked examples of the incident format: symptom, root cause, the class of mistake, and the rung and artifact that now prevent it. Your own `incidents.jsonl` is not tracked. |
| `estate.example.yaml` | The one file you edit, filled in and commented. Copy it, change it, and `./install --estate` sets a machine up without asking a single question. |
| `gateway/` | The running gateway's own working directory, written while it runs. Only `restart_loop.json` is tracked; the rest is process state and is ignored. |
| `gateway/restart_loop.json` | The last-resort restart-loop breaker's boot chain (`hermes-agent/gateway/restart_loop_guard.py`). It survives process death because each boot is a fresh process; once too many boots chain within `max_gap_seconds` the gateway skips auto-resuming the session that keeps killing it, so a human is put back in the loop instead of the crash repeating unattended. |
| `handlers/` | Code the agent runs when a message arrives, rather than when a clock fires. |
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
| `templates/deploy/` | The deployment files that name an app, on the same rule as `templates/bin/`. |
| `templates/deploy/fly/` | The Fly target's generated files. |
| `templates/deploy/fly/finish-cutover.sh.tmpl` | The cutover, with the app it moves to and the app it moves from both read from `estate.yaml`. |
| `templates/deploy/fly/fly.toml.tmpl` | The app configuration. The app name is the one value in it that cannot be shared between estates. |
| `templates/scripts/bootstrap-age-auth.sh.tmpl` | Encrypting the Claude credential, with the target app read from `estate.yaml`. |
| `templates/scripts/check-age-drill.sh.tmpl` | Reading the age drill's verdict, with the app to ask read from `estate.yaml`. |
| `templates/skills/` | One directory per skill. A skill is a prompt with shell commands in it, so each is reviewed as code. |
| `templates/skills/PLATFORM_GATING.md.tmpl` | What each skill needs before it may run. A skill whose platform is missing must fail at the top rather than half-run. |
| `templates/skills/consult/` | The consult skill. |
| `templates/skills/consult/SKILL.md.tmpl` | When a lane may ask a different model, what it must never send, and why exit 3 is a normal answer rather than a fault. |
| `templates/skills/estate-map/` | The estate-map skill. |
| `templates/skills/estate-map/SKILL.md.tmpl` | Print the current shape of the estate — apps, health, repos, open board — which is what you run instead of guessing where something lives. |
| `templates/skills/incident-triage/` | The incident-triage skill. |
| `templates/skills/incident-triage/SKILL.md.tmpl` | Turn a red platform into one GitHub issue with real evidence in it. It never fixes anything, which is what keeps it usable while the fire is lit. |
| `templates/skills/post-mortem/` | The post-mortem skill. |
| `templates/skills/post-mortem/SKILL.md.tmpl` | Close an incident by naming the class of mistake and adding the guard. It runs after the platform is serving again, never during. |
| `templates/skills/pr-discipline/` | The pr-discipline skill. |
| `templates/skills/pr-discipline/SKILL.md.tmpl` | How a change gets made: worktree, reproduce, smallest diff, pull request, stop. It never merges. |
| `templates/skills/screenshot-to-story/` | The screenshot-to-story skill. |
| `templates/skills/screenshot-to-story/SKILL.md.tmpl` | Turn a photo the founder sends into a well-formed issue, for the message that is an image and almost no words. |
| `templates/skills/verify-to-prod/` | The verify-to-prod skill. |
| `templates/skills/verify-to-prod/SKILL.md.tmpl` | Prove a merged change is actually running in production, from two angles, before anything is called done. |
| `tests/` | The guards. Each one is a mistake that already happened here and cannot now happen quietly. |
| `tests/incidents/` | One test per incident, named for its row in the incident ledger. |
| `tests/incidents/README.md` | The rule these files exist under: a post-mortem that adds no test here has not closed its class. |
| `tests/incidents/test_incidents_have_guards.py` | Refuses an incident row that states a lesson without naming the rung and the artifact that enforce it, so no incident closes on a sentence. |
| `tests/test_incident_claim_gate_false_done.py` | Rung 4, named for crew #63: the verification ledger held 0 events while a `DONE:` reached the founder over Telegram, so a false done cost nothing to say. Asserts the stamp fires on an unproven `DONE:` and stays away from a proven one, a doc-only one, `WORKING:`/`BLOCKED:`, and an unknown session — the gate fails open, it never blocks or bounces a reply. |
| `tests/test_evidence_gate_checks_screenshots.py` | Refuses an evidence gate that reads pasted text and not the committed screenshot. Pasted text reads the same whether the command ran or not. |
| `tests/test_features_switch.py` | Refuses a feature flip that edits another block of `estate.yaml`, and an off lane that still gets jobs created. Both happened while the switch was being built. |
| `tests/test_no_runtime_files_are_tracked.py` | Refuses a tracked file that the running agent writes. The repo and the agent's home are the same directory, so this is a live risk on every tick. |
| `tests/test_spec_links_resolve.py` | Refuses a requirement whose `spec` link does not resolve to a real anchor in the spec, which is what keeps the traceability honest rather than decorative. |

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
| `work-agent-go` | `*/20 * * * *` | WORK | Takes the oldest issue labelled `agent-go`, works it in a worktree, opens a pull request, stops. Never merges, never deploys. |
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
        api     200   answering            https://acme-api.fly.dev/health
        web     200   answering            https://acme-web.fly.dev/
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
