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
"$H/.venv/bin/hermes" --version || true

# HERMES_GATEWAY_AUTOSTART is the cutover switch, and it only means something
# because of the block below. This image has no supervisord — the old estate's
# does, and that is where the variable's name comes from. Without this, setting
# it to 1 changed an environment variable and nothing else, and the container
# went on sleeping. Measured 2026-08-22: CMD was ["sleep","infinity"].
if [ "${HERMES_GATEWAY_AUTOSTART:-0}" = "1" ]; then
    # Refuse to start without a resolvable Anthropic credential. On this image
    # the Claude Code Keychain and ~/.claude are both absent, so the only
    # sources that can work are the env vars — see docs/claude-auth.md. Starting
    # anyway gives a gateway that answers Telegram and fails every turn, which
    # looks like a Hermes bug rather than a missing secret.
    if ! "$H/.venv/bin/python" -c "
import sys
sys.path.insert(0, '$H/hermes-agent')
from agent.anthropic_adapter import resolve_anthropic_token
sys.exit(0 if resolve_anthropic_token() else 1)
" 2>/dev/null; then
        echo "[entrypoint] REFUSING to start the gateway: no Anthropic token resolves." >&2
        echo "[entrypoint] Set CLAUDE_CODE_OAUTH_TOKEN (preferred) or ANTHROPIC_API_KEY." >&2
        echo "[entrypoint] See docs/claude-auth.md. Sleeping so the machine stays reachable." >&2
        exec sleep infinity
    fi
    echo "[entrypoint] starting the gateway"
    cd "$H"
    exec "$H/.venv/bin/hermes" gateway run --replace --external-supervisor
fi

echo "[entrypoint] gateway autostart is off; idling"
exec "$@"
