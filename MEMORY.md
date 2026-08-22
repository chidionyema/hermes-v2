# Platform map

Fly.io, region lhr, owner personal. Live: prospector-engine, prospector-store-web
(GET / -> 200), prospector-store-api, prospector-hermes, prospector-searxng.
Suspended, expected: hermes-ci, prospector-ci, prospector-verifier-lab, tie-*.

**prospector-engine is behind a login.** Every path 307s to /login?next=... .
Following that redirect gives 200 - the login page, not a health response. Never
probe it with `curl -L`: it reports green forever, dead app or not. Probe with
no-follow and treat 2xx/3xx/401/403/404 as "answering", 5xx or no answer as down.
prospector-store-api answers 404 on every common health path; its real one is
unknown and worth one issue.

Repos: chidionyema/prospector (the platform, most active), hermes-config (the
estate home), hermes-agent (fork of NousResearch, being replaced by this build),
claude-guards, haworks-platform.

Board: chidionyema/prospector issues. Existing labels include lane: Engine/API/
UI/Ops and P0..P3. New states: triage, agent-go, in-progress, pr-open, merged,
deployed, verified, closed, proposal, post-mortem, needs-local, bench-stale.

Deploy path: PR -> merge to main -> GitHub Actions -> fly deploy. Logs:
`fly logs -a <app> --no-tail`. Status: `fly status -a <app>`.

This build: ~/Documents/code/hermes-v2, branch architect-v2, Hermes v2026.8.19
pinned, model claude-haiku-4-5 everywhere. Old estate at ~/.hermes is frozen and
shares the Telegram bot token, so its gateway must stay off.

Always run commands through bin/hermes. Bare `hermes` points at the OLD estate.
