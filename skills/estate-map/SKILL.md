---
name: estate-map
description: Print the current shape of the estate - Fly apps, health, repos, open board items. Use at the start of any task that touches the platform, and whenever you are about to guess where something lives.
---

# estate-map

Never answer "what is running?" from memory. Run this.

## The apps

```bash
fly apps list
```

Expected live: `prospector-engine`, `prospector-store-web`, `prospector-store-api`,
`prospector-hermes`, `prospector-searxng`.
Expected suspended: `hermes-ci`, `prospector-ci`, `prospector-verifier-lab`, `tie-*`.
A suspended app in the live list, or a live app missing, is an issue.

## Is it serving

```bash
fly status -a prospector-engine
# NO -L. Never follow the redirect.
curl -s -o /dev/null -w '%{http_code}\n' https://prospector-engine.fly.dev/health
curl -s -o /dev/null -w '%{http_code}\n' https://prospector-store-web.fly.dev/
```

**Healthy is not "200".** prospector-engine sits behind a login: every path
returns `307` to `/login?next=...`. Follow that redirect and you get `200` from
the login page, which is green whether the app works or not. Measured
2026-08-22:

```
$ curl -s -o /dev/null -w '%{http_code}' https://prospector-engine.fly.dev/health
307
$ curl -sL -o /dev/null -w '%{http_code} %{url_effective}' https://prospector-engine.fly.dev/health
200 https://prospector-engine.fly.dev/login?next=%2Fhealth
```

So: the server answering for itself is healthy - `2xx`, `3xx`, `401`, `403`,
`404`. A `5xx` or no answer at all is not. `bin/pulse.sh` implements exactly
this and runs every 15 minutes with no model attached.

Known open question: `prospector-store-api` returns 404 on `/`, `/health`,
`/healthz`, `/api/health`, `/openapi.json`, `/docs` and `/v1/health`, measured
2026-08-22. Its real health path is not known. Do not report the API as down
because of a 404 on a path nobody has confirmed. Do open an issue asking for the
path, once.

## The repos

```bash
gh repo list chidionyema --limit 30 --json name,pushedAt,isArchived \
  --jq '.[] | select(.isArchived==false) | "\(.pushedAt[0:10])  \(.name)"' | sort -r
```

`prospector` is the platform. `hermes-config` is this estate's home.

## The board

```bash
gh issue list -R chidionyema/prospector --state open \
  --json number,title,labels --jq '.[] | "#\(.number) \(.title) [\([.labels[].name]|join(","))]"'
```

## Thresholds

These live here, in the skill, not in a script. Changing what counts as
unhealthy is a one-line edit to this file with a diff, reviewable like anything
else. A threshold buried in code is a decision nobody can see.

| what | healthy | open an issue when |
|---|---|---|
| engine /health | 307 to /login | two consecutive 5xx or no-answer, 15 min apart |
| store-web / | 200 | two consecutive 5xx or no-answer |
| machines started | all | any machine not `started` for 10 minutes |
| region | lhr | any machine outside lhr |
| agent-go age | under 3 days | older than 7 days, untouched |
| open issues | any | a jump of more than 20 in a day |

One failed probe is a reading. Two are a fact. The threshold is two, everywhere,
and that is the whole reason the pulse runs every 15 minutes rather than hourly.

## Report shape

Two lines, then stop.

```
Platform: <serving or not>, <the number that says so>
Board: <n> open, <n> in agent-go, <n> pr-open
```
