# Platform map

Fly.io, region lhr, owner personal. Live: prospector-engine (main service,
GET /health -> 200), prospector-store-web (GET / -> 200), prospector-store-api
(health path UNKNOWN - every common path 404s; WATCH should open an issue),
prospector-hermes, prospector-searxng.
Suspended, expected: hermes-ci, prospector-ci, prospector-verifier-lab, tie-*.

Repos: chidionyema/prospector (the platform, most active), hermes-config (the
estate home), hermes-agent (fork of NousResearch, being replaced by this build),
claude-guards, haworks-platform.

Board: chidionyema/prospector issues. Existing labels include lane: Engine/API/
UI/Ops and P0..P3. New states: triage, agent-go, in-progress, pr-open, merged,
deployed, verified, closed, proposal, post-mortem, needs-local, bench-stale.

Deploy path: PR -> merge to main -> GitHub Actions -> fly deploy. Logs:
`fly logs -a <app> --no-tail`. Status: `fly status -a <app>`.

Baselines measured 2026-08-22: engine 1 machine, lhr, started, version 98.

This build: ~/Documents/code/hermes-v2, branch architect-v2, Hermes v2026.8.19
pinned. Old estate at ~/.hermes is frozen and shares the Telegram bot token, so
its gateway must stay off.

Always run commands through bin/hermes. Bare `hermes` points at the OLD estate.
