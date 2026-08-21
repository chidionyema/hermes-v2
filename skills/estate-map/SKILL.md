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
curl -s -o /dev/null -w '%{http_code}\n' https://prospector-engine.fly.dev/health
curl -s -o /dev/null -w '%{http_code}\n' https://prospector-store-web.fly.dev/
```

Healthy is `200` from both, and `fly status` showing state `started` in `lhr`.

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

## Report shape

Two lines, then stop.

```
Platform: <serving or not>, <the number that says so>
Board: <n> open, <n> in agent-go, <n> pr-open
```
