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
  - key: api
    app: acme-api
    url: https://acme-api.fly.dev/health
    expect: "2xx"
    note: ""

models:
  watch: claude-haiku-4-5
  work: claude-haiku-4-5
```

Add a service, change a model, point it at a different repo — then:

```bash
bin/render      # writes your change into all 20 generated files
bin/verify      # proves the result still works
```

`bin/render --check` fails if anyone has hand-edited a generated file, so the
estate and the files describing it cannot quietly drift apart.

---

## Commands

| | |
|---|---|
| `./install` | set up from a clean clone |
| `./bin/verify` | prove it works — 11 checks, a few seconds |
| `./bin/verify --full` | also run all 122 requirement checks |
| `./bin/hermes ...` | the agent itself; **always** use this wrapper, never bare `hermes` |
| `bin/render` | push `estate.yaml` into every generated file |
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

- **122 requirements**, each closing only when a shell command exits 0. Nothing
  is closed by anyone asserting it is closed. `bin/check-requirements.py`.
- **A lesson ladder.** Every incident goes in `estate-evals/incidents.jsonl`
  with the class of mistake it belongs to and the artifact that now prevents it.
  Rung 4 means a machine refuses the mistake; rung 1 means somebody wrote it
  down. Notes are the weakest rung on purpose.
- **An evidence gate in CI.** A pull request body that claims a fix without
  showing the command output is rejected. `ci/evidence-gate.js` has 10 tests,
  each one a way somebody tried to talk their way past it.

```bash
./bin/verify
```

```
  PASS  estate.yaml describes an estate    acme 2
  PASS  generated files match templates    20 checked
  PASS  the agent runs                     Hermes Agent v0.20.5
  PASS  agent is the pinned commit         fcbd1076a9
  PASS  agent home is this directory       /Users/you/acme-agent
  PASS  every model has a price            2 models
  PASS  an Anthropic credential exists     auth.json (hermes auth login)
  PASS  .env is private (mode 600)         mode 600
  PASS  no secrets tracked in git
  PASS  every service answers
        site    200   answering            https://acme-site.fly.dev/
        api     307   answering            https://acme-api.fly.dev/health
  PASS  cron jobs installed                5 jobs
  IDLE  the gateway is not running         start it: ./bin/hermes gateway install

  11 passed, 0 failed
```

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
