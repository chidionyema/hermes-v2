#!/usr/bin/env bash
# Hourly. If a skill changed, commit it. Spec §4.
# The agent edits its own skills. Without this, a skill that quietly rewrote
# itself last Tuesday has no diff and no way back.
set -euo pipefail
H="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$H"
if [ -z "$(git status --porcelain -- skills/)" ]; then
  exit 0
fi
git add -A skills/
HERMES_LANE=claude git -c user.name=hermes -c user.email=hermes@local \
  commit -q -m "skills: drift at $(date -u +%Y-%m-%dT%H:%MZ)" -- skills/
echo "committed skill drift"
