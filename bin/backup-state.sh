#!/usr/bin/env bash
# Nightly off-box backup of the agent's memory. Spec §10.
# state.db holds every session, every lesson and every approval. Losing it is
# losing the estate's memory, and no amount of git history brings it back.
set -euo pipefail
H="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$H/backups"
if [ ! -f "$H/state.db" ]; then
  echo "no state.db yet - nothing to back up"; exit 0
fi
# sqlite3 .backup, not cp: cp of a live database gives a torn file.
sqlite3 "$H/state.db" ".backup '$H/backups/state.db.$STAMP'"
gzip -f "$H/backups/state.db.$STAMP"
ln -sf "state.db.$STAMP.gz" "$H/backups/state.db.latest"
# Off-box. Local disk dying is the case this is for.
if command -v gh >/dev/null && [ -n "${BACKUP_GIST_ID:-}" ]; then
  gh gist edit "$BACKUP_GIST_ID" -a "$H/backups/state.db.$STAMP.gz" || echo "off-box copy failed"
else
  echo "NOTE: local only. Set BACKUP_GIST_ID, or point this at R2, for off-box."
fi
# Keep 14, delete the rest.
ls -1t "$H"/backups/state.db.*.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
echo "backed up to backups/state.db.$STAMP.gz"
