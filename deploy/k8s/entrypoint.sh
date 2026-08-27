#!/usr/bin/env bash
# The container's one entry point (crew#516 CP4, 2026-08-27). The image carries this repo at
# /app/estate: config.yaml, SOUL.md, skills/, scripts/, bin/, templates/, cron/*.jobs -- the
# build. HERMES_HOME is a persistent volume: state.db, sessions/, memories/, cron/jobs.json,
# auth.json -- the state. Every boot copies the build over the volume and leaves the state
# alone, so a new image changes what the agent is and never what it remembers.
#
# Why not run with HERMES_HOME=/app? The Mac gateway (ai.architect.gateway.plist) runs with
# HERMES_HOME = this checkout, and the agent writes state beside the build. On a read-only
# root filesystem that is impossible, and on a writable one a pod restart loses every session.
set -euo pipefail

BUILD=${HERMES_BUILD_DIR:-/app/estate}
: "${HERMES_HOME:=/data}"
export HERMES_HOME
VENV=${HERMES_VENV:-/app/hermes-agent/.venv}
PY="$VENV/bin/python"

# Secrets arrive as files, one per env name, never as pod env (Kyverno secrets-not-from-env-vars
# refuses envFrom on the cluster, crew#341/crew#284). The container exports them itself.
if [ -n "${HERMES_ENV_DIR:-}" ] && [ -d "$HERMES_ENV_DIR" ]; then
  for f in "$HERMES_ENV_DIR"/*; do
    [ -f "$f" ] || continue
    n=$(basename "$f")
    case "$n" in [A-Z_][A-Z0-9_]*) export "$n=$(cat "$f")";; esac
  done
fi

mkdir -p "$HERMES_HOME"
# The build over the volume. --no-preserve keeps the volume's fsGroup ownership; cp never
# touches a path that is not in the build, and no state path is (they are gitignored).
cp -R --no-preserve=ownership,mode "$BUILD"/. "$HERMES_HOME"/
# bin/hermes execs $HERMES_HOME/.venv/bin/hermes; the venv lives with the upstream checkout.
ln -sfn "$VENV" "$HERMES_HOME/.venv"

# The provider credential pool, once. auth.json is rewritten by the agent on every token
# refresh, so it is seeded only when the volume has none: a later secret rotation is
# `rm auth.json` on the volume, never an overwrite of a live refresh token.
if [ ! -s "$HERMES_HOME/auth.json" ] && [ -n "${HERMES_AUTH_JSON:-}" ]; then
  (umask 077; printf '%s' "$HERMES_AUTH_JSON" > "$HERMES_HOME/auth.json")
fi
unset HERMES_AUTH_JSON

cd "$HERMES_HOME"
# estate.yaml is generated and gitignored; the example is the cluster's answer until an
# overlay mounts one at $HERMES_ESTATE_YAML.
if [ -n "${HERMES_ESTATE_YAML:-}" ] && [ -s "$HERMES_ESTATE_YAML" ]; then
  cp "$HERMES_ESTATE_YAML" estate.yaml
elif [ ! -s estate.yaml ]; then
  cp estate.example.yaml estate.yaml
fi
"$PY" bin/render

# The two lanes the Mac ticked (cron/watch.jobs, cron/work.jobs). install-cron is idempotent
# and `--feature` keeps a lane that is off in estate.yaml genuinely inert. The gateway process
# ticks jobs.json itself; there is no second scheduler.
"$PY" bin/install-cron.py cron/watch.jobs --feature watch || echo "entrypoint: watch lane not installed (see above)"
"$PY" bin/install-cron.py cron/work.jobs  --feature work  || echo "entrypoint: work lane not installed (see above)"

exec "$PY" -m hermes_cli.main gateway run "$@"
