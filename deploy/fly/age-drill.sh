#!/usr/bin/env bash
# Does the age fallback still open, and does it still hold the token in use?
#
# One implementation, two callers. The entrypoint runs it once at boot; the
# watcher loop it spawns runs it again whenever the live credential changes and
# every six hours regardless. It used to be inline in the entrypoint, which
# meant it ran at boot and never again -- and the rot it is looking for happens
# on the first token refresh, which is a running-container event. A machine up
# for a week detected nothing for a week.
#
# It never fails anything. The age path is the spare wheel, not the wheel. What
# it does instead is write its verdict to a file on the volume, so something
# outside the container can go red without needing AGE_PRIVATE_KEY -- which is
# invisible to `fly ssh console` and visible only to what the entrypoint
# spawned. See scripts/check-age-drill.sh.
#
# Exit codes are the whole interface:
#   0  ok       ciphertext opens, parses, matches the live token
#   1  fail     it does not open, or opens to something that is not a credential
#   2  drifted  it opens and parses, and holds a DIFFERENT token than the live one
#   3  skipped  no key on this platform, or no ciphertext in this image
set -uo pipefail

H=${HERMES_HOME:-/opt/hermes-v2}
D=${HERMES_STATE_DIR:-/data}
CRED=${HERMES_CRED_FILE:-$D/dot-claude/.credentials.json}
AGE_FILE=${AGE_CIPHERTEXT:-$H/deploy/secrets/claude-credentials.json.age}
STATUS=${AGE_DRILL_STATUS:-$D/age-drill.status}
PY="$H/.venv/bin/python"

# The digest of an access token, never the token. Truncated sha256 is enough to
# say "these two are the same file" and is not a credential.
tok_digest() {
    "$PY" - "$1" <<'PYEOF' 2>/dev/null
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

# The last-resort credential: what answers when the volume file is gone. It is
# deliberately NOT what runs day to day — when the file exists and carries a
# refresh token, _prefer_refreshable_claude_code_token
# (hermes-agent/agent/anthropic_adapter.py:1366-1385) overrides this env var so
# a static token cannot shadow refresh forever. That is correct, and it is also
# why you cannot see this credential working by watching a healthy container.
# So report whether it is there at all. "none" means one lost volume from an
# app that cannot authenticate.
fallback_state() {
    case "${CLAUDE_CODE_OAUTH_TOKEN:-}" in
        '')          echo none ;;
        sk-ant-oat*) echo setup-token ;;
        \{*)         echo json-document-not-a-token ;;
        *)           echo unrecognised ;;
    esac
}

# Written on every run including the skips, because a status file that only
# appears when things are broken is indistinguishable from a checker that never
# ran. The reader wants freshness as much as verdict.
emit() {
    local verdict=$1 detail=$2 age_sha=${3:-} live_sha=${4:-}
    local tmp
    tmp=$(mktemp "${STATUS}.XXXXXX") || return 0
    printf '{"verdict":"%s","detail":"%s","age_digest":"%s","live_digest":"%s","fallback":"%s","checked_at":"%s","epoch":%s}\n' \
        "$verdict" "$detail" "$age_sha" "$live_sha" "$(fallback_state)" \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(date -u +%s)" > "$tmp"
    chmod 644 "$tmp"
    mv -f "$tmp" "$STATUS"
}

say() { echo "[age-drill] $*"; }

if [ -z "${AGE_PRIVATE_KEY:-}" ]; then
    emit skipped "no AGE_PRIVATE_KEY on this platform"
    say "skipped (no AGE_PRIVATE_KEY on this platform)"
    exit 3
fi
if [ ! -f "$AGE_FILE" ]; then
    emit skipped "no ciphertext at $AGE_FILE"
    say "skipped (no ciphertext in this image)"
    exit 3
fi
if ! command -v age >/dev/null 2>&1; then
    emit skipped "age is not installed in this image"
    say "skipped (age is not installed)"
    exit 3
fi

KEY=$(mktemp); chmod 600 "$KEY"
OUT=$(mktemp); chmod 600 "$OUT"
trap 'rm -f "$KEY" "$OUT"' EXIT
printf '%s\n' "$AGE_PRIVATE_KEY" > "$KEY"

if ! age -d -i "$KEY" "$AGE_FILE" > "$OUT" 2>/dev/null; then
    emit fail "AGE_PRIVATE_KEY does not open the ciphertext"
    say "FAIL AGE_PRIVATE_KEY does not open $AGE_FILE" >&2
    exit 1
fi

AGE_SHA=$(tok_digest "$OUT" || true)
LIVE_SHA=$(tok_digest "$CRED" || true)

if [ -z "$AGE_SHA" ]; then
    emit fail "the ciphertext opened but holds no parseable accessToken"
    say "FAIL the ciphertext opened but does not parse" >&2
    exit 1
fi
if [ -n "$LIVE_SHA" ] && [ "$AGE_SHA" != "$LIVE_SHA" ]; then
    # This is the case the boot-only drill could never see. The live credential
    # refreshed, the ciphertext did not, and the spare wheel is now a wheel from
    # a different car. It still opens, so a check that only asks "does it
    # decrypt" reports green.
    emit drifted "the ciphertext holds a different token than the one in use" "$AGE_SHA" "$LIVE_SHA"
    say "DRIFTED it opens, and holds a different token than the one in use (age=$AGE_SHA live=$LIVE_SHA)" >&2
    exit 2
fi

emit ok "opens to the token in use" "$AGE_SHA" "$LIVE_SHA"
say "ok, opens to the token in use ($AGE_SHA)"
exit 0
