#!/usr/bin/env bash
# Is the age fallback on a Fly app still good? Run this from outside the app.
#
# The drill itself runs inside the container, because AGE_PRIVATE_KEY is a Fly
# secret and only what the entrypoint spawned can see it. This reads the verdict
# it leaves on the volume, and it is the half that goes red. A log line in
# `fly logs` that nobody greps is not an alert.
#
# Exit codes:
#   0  the ciphertext opens and holds the token in use, checked recently
#   1  FAIL      it does not open, or opens to something unparseable
#   2  DRIFTED   it opens, and holds a different token than the one in use
#   3  STALE     the verdict is older than the freshness window, or absent --
#                which also covers a container that is not running the watcher
#   4  SKIPPED   no key or no ciphertext on that app
set -uo pipefail

APP=${1:-${FLY_APP:-prospector-hermes-v2}}
MAX_AGE=${MAX_AGE_SECONDS:-43200}   # 12h: the drill runs at least every 6h
STATUS_PATH=${STATUS_PATH:-/data/age-drill.status}

# The same kind of seam as TELEGRAM_API_BASE elsewhere in the estate: unset in
# real use, set by a test that has a status file and no Fly app.
READ_CMD=${AGE_DRILL_READ_CMD:-fly ssh console -a $APP -C "cat $STATUS_PATH"}
RAW=$($READ_CMD 2>/dev/null | tr -d '\r')
if [ -z "$RAW" ]; then
    echo "STALE  $APP: no $STATUS_PATH. The drill has never run, or the machine is down."
    exit 3
fi

PARSED=$(printf '%s' "$RAW" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("unreadable"); print(0); print("the status file is not JSON"); raise SystemExit
print(d.get("verdict", "unreadable"))
print(int(d.get("epoch", 0) or 0))
print(str(d.get("detail", "")).replace("\n", " "))
' 2>/dev/null)
VERDICT=$(printf '%s\n' "$PARSED" | sed -n 1p)
EPOCH=$(printf '%s\n' "$PARSED" | sed -n 2p)
DETAIL=$(printf '%s\n' "$PARSED" | sed -n 3p)
[ -z "${EPOCH//[0-9]/}" ] || EPOCH=0

NOW=$(date -u +%s)
AGE=$(( NOW - ${EPOCH:-0} ))

if [ "$VERDICT" = "unreadable" ] || [ "${EPOCH:-0}" -le 0 ]; then
    echo "STALE  $APP: $STATUS_PATH is there but says nothing usable."
    exit 3
fi
if [ "$AGE" -gt "$MAX_AGE" ]; then
    echo "STALE  $APP: last checked ${AGE}s ago (limit ${MAX_AGE}s). Verdict was '$VERDICT'."
    exit 3
fi

case "$VERDICT" in
    ok)      echo "ok     $APP: $DETAIL (checked ${AGE}s ago)"; exit 0 ;;
    drifted) echo "DRIFT  $APP: $DETAIL (checked ${AGE}s ago)"; exit 2 ;;
    fail)    echo "FAIL   $APP: $DETAIL (checked ${AGE}s ago)"; exit 1 ;;
    skipped) echo "skip   $APP: $DETAIL"; exit 4 ;;
    *)       echo "STALE  $APP: unreadable verdict"; exit 3 ;;
esac
