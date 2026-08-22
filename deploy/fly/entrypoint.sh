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

exec "$@"
