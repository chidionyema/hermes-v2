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

# Identity comes from the platform that runs this container, not from a daemon
# on somebody's laptop. Fly injects secrets into init and its children, and this
# script is init's child — that is how HERMES_GATEWAY_AUTOSTART reaches the
# block below. A `fly ssh console` session is outside that tree and sees none of
# them, which is what made a Fly secret look unreadable from inside the machine.
#
# Two shapes are accepted, and they are NOT interchangeable:
#
#   CLAUDE_CODE_OAUTH_TOKEN   a setup-token string, from `claude setup-token`.
#                             resolve_anthropic_token() returns it directly at
#                             priority 2. Nothing is written to the volume and
#                             there is nothing to refresh.
#   CLAUDE_CREDENTIALS_JSON   the whole Claude Code credentials document. Seeded
#                             onto the volume once, then owned by the container,
#                             which refreshes it and writes it back.
#
# Never put the JSON in CLAUDE_CODE_OAUTH_TOKEN. Priority 2 hands back whatever
# that variable holds, verbatim, as the bearer token, and it is checked before
# the file at priority 4 — so the file would be perfect, resolution would
# "succeed", and every API call would send a JSON document as its credential.
# anthropic_adapter.py:1459-1465, and _prefer_refreshable_claude_code_token at
# :1374 does not rescue it: a JSON blob fails _is_oauth_token and returns None.
mkdir -p "$D/dot-claude"
chmod 700 "$D/dot-claude"

if [ ! -f "$D/dot-claude/.credentials.json" ] && [ -n "${CLAUDE_CREDENTIALS_JSON:-}" ]; then
    echo "[entrypoint] identity: seeding the volume from CLAUDE_CREDENTIALS_JSON"
    printf '%s' "$CLAUDE_CREDENTIALS_JSON" > "$D/dot-claude/.credentials.json"
fi

# The link is remade on every boot because the root filesystem is rebuilt from
# the image on every boot. One made by hand from an ssh session lasts until the
# next restart and no longer.
if [ -d /root/.claude ] && [ ! -L /root/.claude ]; then
    cp -a /root/.claude/. "$D/dot-claude/" 2>/dev/null || true
fi
rm -rf /root/.claude
ln -sfn "$D/dot-claude" /root/.claude
[ -f "$D/dot-claude/.credentials.json" ] && chmod 600 "$D/dot-claude/.credentials.json" || true

if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    echo "[entrypoint] identity: CLAUDE_CODE_OAUTH_TOKEN is set (env, no refresh)"
elif [ -f "$D/dot-claude/.credentials.json" ]; then
    echo "[entrypoint] identity: /root/.claude -> $D/dot-claude (credential file present)"
else
    echo "[entrypoint] identity: no credential from any source" >&2
fi

"$H/.venv/bin/hermes" --version || true

# HERMES_GATEWAY_AUTOSTART is the cutover switch, and it only means something
# because of the block below. This image has no supervisord — the old estate's
# does, and that is where the variable's name comes from. Without this, setting
# it to 1 changed an environment variable and nothing else, and the container
# went on sleeping. Measured 2026-08-22: CMD was ["sleep","infinity"].
if [ "${HERMES_GATEWAY_AUTOSTART:-0}" = "1" ]; then
    # Fail fast rather than start degraded. Founder ruling, 2026-08-22. It is
    # the right call now and was not before: with the credential seeded by the
    # platform at boot, absence is a configuration error, not a race against a
    # laptop daemon that might land the file thirty seconds later.
    #
    # Know what this costs. fly.toml declares no services and no health checks,
    # and there is one machine updated in place, so exiting non-zero is a crash
    # loop on the machine that serves — not Fly holding an old version back. The
    # protection against shipping a broken credential is the cutover script,
    # which proves a real turn before it stops the old gateway.
    if ! "$H/.venv/bin/python" -c "
import sys
sys.path.insert(0, '$H/hermes-agent')
from agent.anthropic_adapter import resolve_anthropic_token
sys.exit(0 if resolve_anthropic_token() else 1)
" 2>/dev/null; then
        echo "[entrypoint] ERROR no Anthropic credential resolves; refusing to start." >&2
        echo "[entrypoint] Set one on the platform, then restart:" >&2
        echo "[entrypoint]   fly secrets set CLAUDE_CODE_OAUTH_TOKEN=... -a <app>" >&2
        echo "[entrypoint] See docs/claude-auth.md." >&2
        exit 1
    fi
    echo "[entrypoint] a credential resolves"

    echo "[entrypoint] starting the gateway"
    cd "$H"
    exec "$H/.venv/bin/hermes" gateway run --replace --external-supervisor
fi

echo "[entrypoint] gateway autostart is off; idling"
exec "$@"
