#!/usr/bin/env bash
# Re-run the age drill while the container is up.
#
# Spawned by the entrypoint, in the background, before it execs the gateway. It
# has to be spawned there and nowhere else: AGE_PRIVATE_KEY is a Fly secret, so
# it reaches the entrypoint and the processes it starts, and is invisible to
# anything attached later with `fly ssh console`. A checker that cannot see the
# key cannot open the ciphertext, so this is the only place the drill can live.
#
# Two triggers, because the failure has two shapes:
#   the live credential changed  -- the refresh is the moment the spare goes
#                                   stale, and it is a running-container event
#   six hours passed             -- the ciphertext can also be replaced from
#                                   outside, and a clock is the only thing that
#                                   catches a change nothing local touched
#
# Nothing here can fail the container. Every path continues the loop.
set -uo pipefail

H=${HERMES_HOME:-/opt/hermes-v2}
D=${HERMES_STATE_DIR:-/data}
CRED=${HERMES_CRED_FILE:-$D/dot-claude/.credentials.json}
DRILL=${AGE_DRILL:-$H/deploy/fly/age-drill.sh}
POLL=${AGE_DRILL_POLL_SECONDS:-300}
EVERY=${AGE_DRILL_INTERVAL_SECONDS:-21600}

stamp() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || echo 0; }

last_run=$(date -u +%s)
last_cred=$(stamp "$CRED")

while :; do
    sleep "$POLL" || exit 0
    now=$(date -u +%s)
    cred=$(stamp "$CRED")
    why=""
    if [ "$cred" != "$last_cred" ]; then
        why="the live credential changed"
    elif [ $((now - last_run)) -ge "$EVERY" ]; then
        why="${EVERY}s since the last check"
    fi
    [ -z "$why" ] && continue

    echo "[age-drill-watch] running: $why"
    bash "$DRILL" || true
    last_run=$(date -u +%s)
    last_cred=$cred
done
