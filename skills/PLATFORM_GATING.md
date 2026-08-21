# Platform gating

Skills declare what they need. A skill whose platform is missing must fail
loudly at the top, not half-run and leave the estate in a state nobody expected.

| skill | needs | check |
|---|---|---|
| estate-map | `fly`, `gh`, `curl` | `command -v fly gh curl` |
| incident-triage | `fly`, `gh` | `command -v fly gh` |
| pr-discipline | `git`, `gh`, repo at ~/Documents/code/prospector | `test -d ~/Documents/code/prospector/.git` |
| screenshot-to-story | `gh`, `rg` | `command -v gh rg` |
| verify-to-prod | `fly`, `gh`, `curl` | `command -v fly gh curl` |
| post-mortem | `fly`, `gh` | `command -v fly gh` |

WATCH runs on the cheap lane and read-only. It may run estate-map,
incident-triage, screenshot-to-story. It may not run pr-discipline,
verify-to-prod or post-mortem, because all three write.

WORK may run all six. WORK still never merges and never deploys.

If a check fails, say which binary is missing and stop. Do not install anything
to get past a gate - installing on the fly is how an estate drifts.
