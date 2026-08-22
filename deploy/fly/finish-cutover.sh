#!/usr/bin/env bash
# Move the Telegram gateway from prospector-hermes to prospector-hermes-v2,
# prove it answers, and undo it automatically if it does not.
#
#     ./deploy/fly/finish-cutover.sh
#
# This script handles no secrets. It cannot read one, it never asks for one, and
# it has no flag that takes one. Identity belongs to the Mac and is carried to
# the volume by ~/.local/bin/hermes-auth-bridge, which runs under launchd every
# four hours. This script only checks whether that has happened yet.
#
# The history behind that split is in docs/claude-auth.md: four separate
# permission refusals, all of them on an agent trying to be the courier between
# a human's keychain and a container. Removing the courier removed the problem.
set -euo pipefail

NEW=prospector-hermes-v2
OLD=prospector-hermes
CRED=/data/dot-claude/.credentials.json

# Everything printed here also lands in a file, so a failed run is diagnosed
# from the log rather than from someone retyping their screen. Nothing secret is
# printed, so the log is safe to hand over.
LOG=${CUTOVER_LOG:-/tmp/finish-cutover.log}
if [ -z "${CUTOVER_TEEING:-}" ]; then
    export CUTOVER_TEEING=1
    printf 'logging this run to %s\n' "$LOG"
    set +e
    "$0" "$@" 2>&1 | tee "$LOG"
    RC=${PIPESTATUS[0]}
    set -e
    printf '\nfull log: %s  (exit %s)\n' "$LOG" "$RC"
    exit "$RC"
fi

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
fail() { printf '\033[31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

say "1/5  is there a credential on the volume?"
if fly ssh console -a "$NEW" -C "/bin/sh -c 'test -f $CRED'" >/dev/null 2>&1; then
    echo "yes: $CRED"
    OUT=$(fly ssh console -a "$NEW" -C "/opt/hermes-v2/.venv/bin/python -c 'import sys; sys.path.insert(0,\"/opt/hermes-v2/hermes-agent\"); from agent.anthropic_adapter import resolve_anthropic_token as r; t=r(); print(\"RESOLVED\", len(t) if t else 0)'" 2>&1 \
          | tr -d '\r' | grep -o 'RESOLVED [0-9]*' | head -1)
    echo "container resolver: ${OUT:-no answer}"
else
    warn "no credential on the volume yet."
    warn "The bridge puts it there. Run it now, or wait up to four hours:"
    warn "    ~/.local/bin/hermes-auth-bridge --force"
    warn "Continuing anyway — the gateway starts degraded and picks the credential"
    warn "up when it lands, rather than blocking this cutover on it."
fi

say "2/5  clear the stale state file"
# The volume was restored from a backup of the founder's laptop, so
# /data/gateway_state.json arrived already populated: argv pointing at
# /Users/chidionyema/code/hermes-v2, hermes_home at ~/Documents/code/hermes-v2,
# written 2026-08-22T08:59Z by a process that has never run in this container.
# Polling that for "connected" reads a laptop's history as this machine's
# present. It happened to say "disconnected", so it would have timed out rather
# than lied. That is luck, not design.
fly ssh console -a "$NEW" -C "/bin/sh -c 'rm -f /data/gateway_state.json'" >/dev/null 2>&1
echo "cleared; the next state file is written by this boot"

say "3/5  stop the old gateway"
# Before starting the new one, not after. One bot token allows one long poller;
# run two and they take each other's updates and both look broken (crew #15).
fly ssh console -a "$OLD" -C "supervisorctl stop gateway" 2>&1 | tail -2 || true
# supervisorctl exits 3 for a STOPPED program; that is the answer, not an error.
fly ssh console -a "$OLD" -C "supervisorctl status gateway" 2>&1 | tail -1 || true

say "4/5  start the new gateway"
# Setting a secret restarts the machine, and entrypoint.sh reads the flag on boot.
fly secrets set HERMES_GATEWAY_AUTOSTART=1 -a "$NEW" >/dev/null
echo "waiting for it to come up"
CONNECTED=0
for i in $(seq 1 20); do
    sleep 15
    RAW=$(fly ssh console -a "$NEW" \
        -C "/bin/sh -c 'cat /data/gateway_state.json 2>/dev/null || true'" 2>/dev/null | tr -d '\r')
    # A state file naming a path that only exists on a Mac is not this container
    # reporting on itself. Refuse it rather than reading it as a live answer.
    case "$RAW" in
        *'/Users/chidionyema'*) fail "the state file names a laptop path; that is not a live reading" ;;
    esac
    STATE=$(printf %s "$RAW" | grep -o '"state"[[:space:]]*:[[:space:]]*"[a-z]*"' | head -2 | tr '\n' ' ')
    printf '  %3ds  %s\n' "$((i*15))" "${STATE:-no state file yet}"
    case "$STATE" in *'"connected"'*) CONNECTED=1; break ;; esac
done

say "5/5  verdict"
if [ "$CONNECTED" = 1 ]; then
    echo "the new gateway is connected to Telegram."
    echo "send it a message. if it answers, the next step is:"
    echo "    fly apps suspend $OLD          # keeps the volume; nothing is destroyed"
    exit 0
fi

printf '\033[31mthe new gateway did not connect. rolling back.\033[0m\n'
fly secrets set HERMES_GATEWAY_AUTOSTART=0 -a "$NEW" >/dev/null
fly ssh console -a "$OLD" -C "supervisorctl start gateway" 2>&1 | tail -2 || true
# supervisorctl exits 3 for a STOPPED program; that is the answer, not an error.
fly ssh console -a "$OLD" -C "supervisorctl status gateway" 2>&1 | tail -1 || true
echo "the old gateway is back. logs from the new one:"
fly ssh console -a "$NEW" -C "/bin/sh -c 'tail -40 /data/logs/gateway.log 2>/dev/null || true'" 2>&1 | tail -40
exit 1
