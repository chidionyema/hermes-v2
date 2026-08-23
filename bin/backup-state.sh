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
# Off-box. Local disk dying is the case this is for, and a second copy on the same
# disk is not a second copy.
#
# This does not upload anything itself. The estate already runs one backup engine
# (prospector-live ops/automations/offsite_backup.py, launchd com.prospector.offsite-backup,
# nightly at 03:50) which holds the R2 credentials, verifies every copy with PRAGMA
# integrity_check before it counts, prunes to a retention set in git, and grades the
# freshness of the result from a second always-on host. state.db is declared there as the
# `hermes-state` source. Writing a second uploader here would mean a second set of
# credentials, a second retention policy and two half-working backups.
#
# The gist branch that used to be here is gone. A gist is a text-sharing surface with no
# integrity check, no retention and a default that is one flag away from public; the
# estate's memory does not go there.
OFFSITE="$HOME/Documents/code/prospector-live"
if [ -x "$OFFSITE/.venv/bin/python" ]; then
  ( cd "$OFFSITE" && ./.venv/bin/python -m ops.automations.offsite_backup --fix ) \
    || echo "off-box copy failed - see docs/RUNBOOKS.md#offsite-backup"
else
  echo "NOTE: local only. The offsite engine is not at $OFFSITE." >&2
  exit 1
fi
# Keep 14, delete the rest.
ls -1t "$H"/backups/state.db.*.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
echo "backed up to backups/state.db.$STAMP.gz"
