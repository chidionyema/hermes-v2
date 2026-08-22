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

# ---------------------------------------------------------------- identity
# Identity comes from the platform that runs this container, never from a
# daemon on somebody's laptop. Sources are tried in order and the first one
# that yields a usable credential wins.
#
#   1. the volume          a previous boot seeded it, or the container refreshed it
#   2. Doppler             DOPPLER_TOKEN set and the doppler binary present
#   3. an age-encrypted    AGE_PRIVATE_KEY set; the ciphertext is committed to
#      file in the repo    the repo, so the credential travels in the git bundle
#   4. CLAUDE_CREDENTIALS_JSON   the whole document, straight from a platform secret
#   5. CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY   read from the environment
#                                                    by the resolver itself
#
# Switching source: the volume wins over all of them, because the file there is
# the *refreshed* token and any seed is older. Set HERMES_AUTH_RESEED=1 to
# force a re-seed from 2-4 on the next boot.
mkdir -p "$D/dot-claude"
chmod 700 "$D/dot-claude"

# The root filesystem is rebuilt from the image on every boot, so this is made
# every boot. One made by hand from an ssh session lasts until the next restart.
if [ -d /root/.claude ] && [ ! -L /root/.claude ]; then
    cp -a /root/.claude/. "$D/dot-claude/" 2>/dev/null || true
fi
rm -rf /root/.claude
ln -sfn "$D/dot-claude" /root/.claude

CRED="$D/dot-claude/.credentials.json"

# A file that is not a credentials document is worse than no file: source 1
# would win with it on every future boot, so a bad seed poisons the volume
# permanently and no amount of fixing the secret would take effect.
cred_is_valid() {
    [ -f "$1" ] || return 1
    "$H/.venv/bin/python" - "$1" <<'PYEOF' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
o = d.get("claudeAiOauth", d) if isinstance(d, dict) else {}
sys.exit(0 if isinstance(o, dict) and o.get("accessToken") else 1)
PYEOF
}

if [ -f "$CRED" ] && ! cred_is_valid "$CRED"; then
    echo "[entrypoint] identity: $CRED is not a credentials document; setting it aside" >&2
    mv -f "$CRED" "$CRED.rejected"
fi

if [ -n "${HERMES_AUTH_RESEED:-}" ] && [ -f "$CRED" ]; then
    echo "[entrypoint] identity: HERMES_AUTH_RESEED set; re-seeding from the platform"
    rm -f "$CRED"
fi

if [ -f "$CRED" ]; then
    echo "[entrypoint] identity: using the credential already on the volume"

elif [ -n "${DOPPLER_TOKEN:-}" ] && command -v doppler >/dev/null 2>&1; then
    echo "[entrypoint] identity: seeding from Doppler"
    doppler secrets get CLAUDE_CREDENTIALS_JSON --plain > "$CRED" || rm -f "$CRED"

elif [ -n "${AGE_PRIVATE_KEY:-}" ] && [ -f "$H/deploy/secrets/claude-credentials.json.age" ] \
     && command -v age >/dev/null 2>&1; then
    echo "[entrypoint] identity: decrypting the age-encrypted credential"
    # The key reaches age on a file descriptor, never a command line: an argv is
    # readable by every process on the box via /proc.
    AGE_KEY_FILE=$(mktemp)
    chmod 600 "$AGE_KEY_FILE"
    printf '%s\n' "$AGE_PRIVATE_KEY" > "$AGE_KEY_FILE"
    age -d -i "$AGE_KEY_FILE" "$H/deploy/secrets/claude-credentials.json.age" > "$CRED" || rm -f "$CRED"
    rm -f "$AGE_KEY_FILE"

elif [ -n "${CLAUDE_CREDENTIALS_JSON:-}" ]; then
    echo "[entrypoint] identity: seeding from CLAUDE_CREDENTIALS_JSON"
    printf '%s' "$CLAUDE_CREDENTIALS_JSON" > "$CRED"
fi

# CLAUDE_CODE_OAUTH_TOKEN and ANTHROPIC_API_KEY are deliberately NOT written to
# the file. resolve_anthropic_token() reads both from the environment directly,
# at priorities 2 and 3, ahead of the file at priority 4
# (anthropic_adapter.py:1452-1471). Writing either one into .credentials.json
# produces a file that is not JSON, which then wins source 1 forever, while the
# env var the resolver actually uses keeps working — so the breakage only shows
# up much later, on the machine where the env var is gone.

if [ -f "$CRED" ]; then
    chmod 600 "$CRED"
    cred_is_valid "$CRED" || echo "[entrypoint] identity: WARNING the seeded credential does not parse" >&2
fi

# Drill the age fallback on every boot, without touching the live credential.
# A fallback nobody has exercised is not a fallback, it is a hope, and the day
# it is needed is the worst day to find out the key is wrong or the ciphertext
# is stale. This decrypts to a scratch file, compares the access token against
# the one actually in use, logs one line, and deletes it. It never fails the
# boot: the age path is the spare wheel, not the wheel.
tok_digest() {
    "$H/.venv/bin/python" - "$1" <<'PYEOF' 2>/dev/null
import json, sys, hashlib
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
o = d.get("claudeAiOauth", d) if isinstance(d, dict) else {}
t = o.get("accessToken") or ""
if not t:
    sys.exit(1)
print(hashlib.sha256(t.encode()).hexdigest()[:16])
PYEOF
}

AGE_FILE="$H/deploy/secrets/claude-credentials.json.age"
if [ -n "${AGE_PRIVATE_KEY:-}" ] && [ -f "$AGE_FILE" ] && command -v age >/dev/null 2>&1; then
    DRILL_KEY=$(mktemp); chmod 600 "$DRILL_KEY"
    printf '%s\n' "$AGE_PRIVATE_KEY" > "$DRILL_KEY"
    DRILL_OUT=$(mktemp); chmod 600 "$DRILL_OUT"
    if age -d -i "$DRILL_KEY" "$AGE_FILE" > "$DRILL_OUT" 2>/dev/null; then
        DRILL_SHA=$(tok_digest "$DRILL_OUT" || true)
        LIVE_SHA=$(tok_digest "$CRED" || true)
        if [ -z "$DRILL_SHA" ]; then
            echo "[entrypoint] age drill: FAIL the ciphertext opened but does not parse" >&2
        elif [ -n "$LIVE_SHA" ] && [ "$DRILL_SHA" != "$LIVE_SHA" ]; then
            echo "[entrypoint] age drill: ok, but it holds a different token than the one in use (age=$DRILL_SHA live=$LIVE_SHA)"
        else
            echo "[entrypoint] age drill: ok, opens to the token in use ($DRILL_SHA)"
        fi
    else
        echo "[entrypoint] age drill: FAIL AGE_PRIVATE_KEY does not open $AGE_FILE" >&2
    fi
    rm -f "$DRILL_KEY" "$DRILL_OUT"
elif [ -f "$AGE_FILE" ]; then
    echo "[entrypoint] age drill: skipped (no AGE_PRIVATE_KEY on this platform)"
fi

# Report the last-resort credential, the one that answers when the volume file
# is gone. It is deliberately NOT what runs day to day: when the file exists and
# carries a refresh token, _prefer_refreshable_claude_code_token
# (anthropic_adapter.py:1366-1385) overrides this env var so a static token
# cannot shadow refresh forever. That is correct, and it means you cannot see
# this credential working by watching a healthy container — so say at boot
# whether it is there at all, which is the only cheap moment to find out.
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    case "$CLAUDE_CODE_OAUTH_TOKEN" in
        sk-ant-oat*) echo "[entrypoint] fallback: CLAUDE_CODE_OAUTH_TOKEN is set and looks like a setup-token" ;;
        \{*)         echo "[entrypoint] fallback: WARNING CLAUDE_CODE_OAUTH_TOKEN holds a JSON document, not a token. It will be sent verbatim as a bearer credential. Use CLAUDE_CREDENTIALS_JSON for a document." >&2 ;;
        *)           echo "[entrypoint] fallback: WARNING CLAUDE_CODE_OAUTH_TOKEN is set but is not a recognised setup-token" >&2 ;;
    esac
else
    echo "[entrypoint] fallback: none. If the volume file is lost, this container cannot authenticate."
    echo "[entrypoint]           fix: claude setup-token, then fly secrets set CLAUDE_CODE_OAUTH_TOKEN=... -a <app>"
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
        echo "[entrypoint] Tried: the volume, Doppler, the age file, and the environment." >&2
        echo "[entrypoint] Set one on the platform, then restart. Pick one:" >&2
        echo "[entrypoint]   age:     fly secrets set AGE_PRIVATE_KEY='<key>' -a <app>" >&2
        echo "[entrypoint]   doppler: fly secrets set DOPPLER_TOKEN='<token>' -a <app>" >&2
        echo "[entrypoint]   direct:  fly secrets set CLAUDE_CODE_OAUTH_TOKEN='<setup-token>' -a <app>" >&2
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
