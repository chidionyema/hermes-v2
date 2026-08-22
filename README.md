# The Architect

An engineering agent that watches your production estate, tells you when it
breaks, and opens pull requests you approve from your phone.

It is one directory. You clone it, answer five questions, and it runs. Point it
at a different estate and it is a different agent, with no code changes.

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
| `cron/work.jobs` | The WORK schedule, same format. Deliberately **not** installed by `./install` — WORK opens pull requests, so you start it by hand. |
| `egress-allowlist.txt` | Every host this estate is allowed to reach: your services, `egress_extra`, and the APIs it needs. Everything else is refused. |
| `handlers/screenshot_to_issue.py` | Turns a screenshot sent over chat into a well-formed GitHub issue, so a photo of a broken screen becomes work. |
| `profiles/watch/MEMORY.md` | The WATCH lane's own memory. Seeded from the same platform map, then diverges as that lane learns. |
| `profiles/watch/egress-allowlist.txt` | WATCH's narrower allowlist. Logs are attacker-influenceable text, so this is a control, not a matter of the agent's judgement. |
| `profiles/work/MEMORY.md` | The WORK lane's own memory, seeded the same way. |
| `scripts/pulse.sh` | The identical pulse script where the scheduler looks for it. `cron create --script pulse.sh` resolves in `scripts/`; `bin/` is where a human types it. |
| `skills/PLATFORM_GATING.md` | What each skill needs before it may run. A skill whose platform is missing must fail at the top, not half-run. |
| `skills/estate-map/SKILL.md` | Print the current shape of the estate — apps, health, repos, open board. Run before guessing where anything lives. |
| `skills/incident-triage/SKILL.md` | Turn a red platform into one GitHub issue with real evidence in it. Never fixes anything. |
| `skills/post-mortem/SKILL.md` | Close an incident by naming the class of mistake and adding a guard. Runs after the platform is serving again, never during. |
| `skills/pr-discipline/SKILL.md` | How a change is made: worktree, reproduce, smallest diff, pull request, stop. Never merges. |
| `skills/screenshot-to-story/SKILL.md` | Turn a photo the founder sends into a well-formed issue, when the message has an image and almost no words. |
| `skills/verify-to-prod/SKILL.md` | Prove a merged change is actually running in production, before anything is called done. |

<!-- /files -->

---

## The schedule

Seven jobs. `./install` creates the five WATCH ones and stops there. The two WORK
jobs open pull requests on a live repository, so nothing starts them but you:

```bash
bin/install-cron.py cron/work.jobs
```

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
| `bin/render` | push `estate.yaml` into every generated file |
| `bin/check-readme.py` | fail if this README no longer lists every generated file and cron job |
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
  table above against what `bin/render` actually generates, and the schedule
  table against `templates/cron/*.jobs.tmpl`. Add a generated file or change a
  cron schedule without saying so here and `./bin/verify` fails. It checks the
  half a machine can know; what a file *does* is prose, and prose is on you.
- **An evidence gate in CI.** A pull request body that claims a fix without
  showing the command output is rejected. `ci/evidence-gate.js` has 10 tests,
  each one a way somebody tried to talk their way past it.

```bash
./bin/verify
```

```
  PASS  estate.yaml describes an estate    acme 2
  PASS  generated files match templates    21 checked
  PASS  README describes what ships        21 files, 7 jobs
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
