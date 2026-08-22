#!/usr/bin/env bash
# hermes-v2 keeps code and state in ONE directory -- bin/hermes exports
# HERMES_HOME=<repo root>. A Fly volume mounted over that root would hide the
# code, so the volume mounts at /data and the writable names are symlinked into
# it. The image stays immutable; everything that survives a deploy is on /data.
set -euo pipefail

H=/opt/hermes-v2
D=/data

# Each of these is written at runtime and must outlive a release.
STATEFUL=(state.db kanban.db logs memories sessions backups cache
          auth.json channel_directory.json gateway_state.json)

mkdir -p "$D"
for name in "${STATEFUL[@]}"; do
    # First boot: move whatever the image baked in onto the volume, once.
    if [ ! -e "$D/$name" ] && [ -e "$H/$name" ] && [ ! -L "$H/$name" ]; then
        mv "$H/$name" "$D/$name"
    fi
    # Still nothing there? Create the right kind of empty thing.
    if [ ! -e "$D/$name" ]; then
        case "$name" in
            *.db|*.json) : > "$D/$name" ;;
            *)           mkdir -p "$D/$name" ;;
        esac
    fi
    rm -rf "$H/$name"
    ln -sfn "$D/$name" "$H/$name"
done

# .env is a secret and never lands in the image (.dockerignore). Fly injects the
# values as environment variables; hermes reads either, and env wins.
[ -f "$D/.env" ] && ln -sfn "$D/.env" "$H/.env" || true

echo "[entrypoint] state linked to $D"

# Identity is not state and is not code, so it is handled apart from both.
# ~/.local/bin/hermes-auth-bridge runs on the founder's Mac under launchd and
# writes the Claude credential to /data/dot-claude/.credentials.json. The
# container reads it at $HOME/.claude/.credentials.json, and $HOME is /root.
#
# The link has to be made here, on every boot. The root filesystem is recreated
# from the image each time the machine starts, so a link made by hand from an
# ssh session survives until the next restart and no longer. Measured
# 2026-08-22: the bridge created it at 14:08, and it existed only because
# nothing had restarted the machine since.
mkdir -p "$D/dot-claude"
chmod 700 "$D/dot-claude"
if [ -d /root/.claude ] && [ ! -L /root/.claude ]; then
    # An image that baked something in keeps it, once, on the volume.
    cp -a /root/.claude/. "$D/dot-claude/" 2>/dev/null || true
fi
rm -rf /root/.claude
ln -sfn "$D/dot-claude" /root/.claude
[ -f "$D/dot-claude/.credentials.json" ] && chmod 600 "$D/dot-claude/.credentials.json" || true
if [ -f /root/.claude/.credentials.json ]; then
    echo "[entrypoint] identity: /root/.claude -> $D/dot-claude (credential present)"
else
    echo "[entrypoint] identity: /root/.claude -> $D/dot-claude (no credential yet)"
fi
"$H/.venv/bin/hermes" --version || true

# HERMES_GATEWAY_AUTOSTART is the cutover switch, and it only means something
# because of the block below. This image has no supervisord — the old estate's
# does, and that is where the variable's name comes from. Without this, setting
# it to 1 changed an environment variable and nothing else, and the container
# went on sleeping. Measured 2026-08-22: CMD was ["sleep","infinity"].
if [ "${HERMES_GATEWAY_AUTOSTART:-0}" = "1" ]; then
    # Identity arrives from outside this container. ~/.local/bin/hermes-auth-bridge
    # runs on the founder's Mac under launchd and writes the Claude credential to
    # /data/dot-claude/.credentials.json, which /root/.claude points at. A boot
    # can therefore land before the credential does — after a restore, after a
    # new volume, or in the four-hour gap between bridge runs.
    #
    # This used to refuse and sleep, on the reasoning that a gateway answering
    # Telegram and failing every turn looks like a Hermes bug. That reasoning
    # was half right and the remedy was wrong: refusing makes a missing file
    # into a dead machine, and the cutover that is waiting on it cannot even
    # verify. Wait a while, then start anyway. resolve_anthropic_token() reads
    # the file each time it is called, so a gateway started without one picks
    # the credential up when the bridge lands it, with no restart.
    has_token() {
        "$H/.venv/bin/python" -c "
import sys
sys.path.insert(0, '$H/hermes-agent')
from agent.anthropic_adapter import resolve_anthropic_token
sys.exit(0 if resolve_anthropic_token() else 1)
" 2>/dev/null
    }

    if has_token; then
        echo "[entrypoint] an Anthropic credential resolves"
    else
        echo "[entrypoint] no Anthropic credential yet; waiting up to 300s for the bridge" >&2
        for _ in $(seq 1 30); do
            sleep 10
            if has_token; then break; fi
        done
        if has_token; then
            echo "[entrypoint] the credential arrived"
        else
            echo "[entrypoint] DEGRADED: starting without an Anthropic credential." >&2
            echo "[entrypoint] Telegram will connect; turns will fail until one lands." >&2
            echo "[entrypoint] On the Mac, run: ~/.local/bin/hermes-auth-bridge --force" >&2
            echo "[entrypoint] See docs/claude-auth.md." >&2
        fi
    fi

    echo "[entrypoint] starting the gateway"
    cd "$H"
    exec "$H/.venv/bin/hermes" gateway run --replace --external-supervisor
fi

echo "[entrypoint] gateway autostart is off; idling"
exec "$@"
