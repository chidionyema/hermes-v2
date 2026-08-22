#!/usr/bin/env bash
# Finish the hermes-v2 cutover: put a Claude credential on the new app, move the
# Telegram gateway across, prove it answers, and undo it automatically if it
# does not.
#
#     ./deploy/fly/finish-cutover.sh --copy-api-key   # simplest; no paste, no browser
#     ./deploy/fly/finish-cutover.sh --keychain       # subscription billing instead
#     ./deploy/fly/finish-cutover.sh                  # prompts for the token
#     ./deploy/fly/finish-cutover.sh --token-only     # stop after the secret
#     ./deploy/fly/finish-cutover.sh --skip-token     # secret is already set; just flip
#     claude setup-token | ./deploy/fly/finish-cutover.sh --token-stdin
#
# It needs a real terminal to prompt in. Run through a wrapper that gives it no
# TTY — Claude Code's `!` runner, a CI step, `nohup` — and it now says so and
# exits instead of blocking forever on a prompt nobody can see. Measured
# 2026-08-22: the first run hung exactly there.
#
# The token is read from a silent prompt. It is never echoed, never written to a
# file, never passed on a command line where `ps` could read it, and never put
# in shell history. What gets printed is its length and the first 12 hex of its
# sha256, which is enough to compare two copies and not enough to be one.
#
# Why a setup-token and not the Keychain's accessToken: see docs/claude-auth.md.
set -euo pipefail

NEW=prospector-hermes-v2
OLD=prospector-hermes
TOKEN_ONLY=0
SKIP_TOKEN=0
TOKEN_STDIN=0
COPY_API_KEY=0
KEYCHAIN=0
for a in "$@"; do
    case "$a" in
        --token-only)  TOKEN_ONLY=1 ;;
        --copy-api-key) COPY_API_KEY=1 ;;
        --keychain)    KEYCHAIN=1 ;;
        --skip-token)  SKIP_TOKEN=1 ;;
        --token-stdin) TOKEN_STDIN=1 ;;
        *) printf 'unknown option: %s\n' "$a" >&2; exit 2 ;;
    esac
done

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
fail() { printf '\033[31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

# The gateway's own answer to "can you reach Claude". Prints a length, not a token.
REMOTE_CHECK='
import sys
sys.path.insert(0, "/opt/hermes-v2/hermes-agent")
from agent.anthropic_adapter import resolve_anthropic_token
t = resolve_anthropic_token()
print("RESOLVED" if t else "NONE", len(t) if t else 0)
'

say "1/6  Claude credential"
if [ "$COPY_API_KEY" = 1 ]; then
    # This mode existed and could never have worked. Kept as a refusal so the
    # next person to reach for it gets the reason instead of the dead end.
    #
    # Measured 2026-08-22 on prospector-hermes: a `fly ssh console` shell has
    # ANTHROPIC_API_KEY absent from its environment, and running the gateway's
    # own resolve_anthropic_token() in that shell returns NONE. Fly injects a
    # secret into the machine's init and its supervisord children, not into an
    # ssh session. Fly also has no API that reads a secret's value back — the
    # `secrets list` output is names and digests.
    #
    # So the only copy of that key on the estate is inside the address space of
    # a running process. Reading it back out is not a thing this script should
    # be doing, and it is not necessary: --keychain gets a credential from a
    # place where the value is legitimately available.
    fail "--copy-api-key cannot work, and never could.
  ANTHROPIC_API_KEY is absent from an ssh shell on $OLD (checked: resolve_anthropic_token -> NONE),
  and Fly has no API that reads a secret value back. The key exists only inside the
  running gateway process.
  Use instead:  $0 --keychain        (see docs/claude-auth.md)"
elif [ "$KEYCHAIN" = 1 ]; then
    # Put this Mac's Claude Code credential in the container so it bills against
    # the subscription. It carries a refreshToken, so hermes refreshes it in
    # place — resolve_anthropic_token source #4.
    #
    # The caveat, stated because it is easy to be surprised by: a refresh may
    # rotate the token, and if it does, THIS MAC'S Claude Code login can go
    # stale and need signing in again. Nothing is lost, it is just a re-login.
    command -v security >/dev/null || fail "the security command is missing; this mode is macOS only"
    CREDS=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null || true)
    [ -n "$CREDS" ] || fail "no Claude Code credential in the Keychain. Run: claude setup-token"
    printf 'credential: %s bytes, sha256 %s\n' \
        "${#CREDS}" "$(printf %s "$CREDS" | shasum -a 256 | cut -c1-12)"
    TMP=$(mktemp -t cc-creds); trap 'rm -f "$TMP"' EXIT
    printf '%s' "$CREDS" > "$TMP"; unset CREDS
    # /root/.claude has to be a symlink onto the volume or the refreshed token is
    # lost on the next restart. `ln -sfn` onto an existing real DIRECTORY quietly
    # makes /root/.claude/dot-claude instead, so remove a real directory first —
    # and only a real one, never a symlink that is already correct.
    fly ssh console -a "$NEW" -C "/bin/sh -c '
        mkdir -p /data/dot-claude
        if [ -d /root/.claude ] && [ ! -L /root/.claude ]; then
            cp -a /root/.claude/. /data/dot-claude/ 2>/dev/null || true
            rm -rf /root/.claude
        fi
        ln -sfn /data/dot-claude /root/.claude
        rm -f /data/dot-claude/.credentials.json
        printf "link: "; ls -ld /root/.claude
    '" 2>&1 | tail -1
    printf 'put %s /data/dot-claude/.credentials.json\n' "$TMP" | fly ssh sftp shell -a "$NEW" 2>&1 | tail -1
    rm -f "$TMP"; trap - EXIT
    SKIP_TOKEN=1
    echo "installed at /root/.claude/.credentials.json (on the volume)"
elif [ "$SKIP_TOKEN" = 1 ]; then
    echo "skipping — you set CLAUDE_CODE_OAUTH_TOKEN yourself"
elif [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    TOKEN="$CLAUDE_CODE_OAUTH_TOKEN"
    echo "using CLAUDE_CODE_OAUTH_TOKEN from this shell"
elif [ "$TOKEN_STDIN" = 1 ]; then
    IFS= read -r TOKEN || true
    TOKEN=$(printf %s "$TOKEN" | tr -d '[:space:]')
elif { exec 3</dev/tty; } 2>/dev/null; then
    cat <<'HOWTO'
Run this in another terminal, then paste the token it prints:

    claude setup-token

It is a long-lived OAuth token tied to your Claude subscription. Hermes prefers
it over ANTHROPIC_API_KEY (resolve_anthropic_token, source #2 beats #3), so the
container bills against the subscription and not per token.
HOWTO
    printf 'token (input hidden): '
    IFS= read -rs TOKEN <&3
    exec 3<&-
    printf '\n'
else
    cat >&2 <<'NOTTY'
This has no terminal to prompt in, so it will not ask — it would hang where you
could not see it.

Pick one:

  1. Run it in a real terminal window:
         cd ~/dev/code/hermes-v2 && ./deploy/fly/finish-cutover.sh

  2. Set the secret yourself, then let this do the rest:
         claude setup-token
         fly secrets set CLAUDE_CODE_OAUTH_TOKEN=<paste> -a prospector-hermes-v2
         ./deploy/fly/finish-cutover.sh --skip-token

  3. Pipe it in:
         ./deploy/fly/finish-cutover.sh --token-stdin   # then paste, then ctrl-D
NOTTY
    exit 2
fi

if [ "$SKIP_TOKEN" = 0 ]; then
    [ -n "${TOKEN:-}" ] || fail "no token given"
    case "$TOKEN" in
        sk-ant-*) ;;
        *) fail "that does not start with sk-ant-. Nothing sent. Re-run with the setup-token." ;;
    esac
    printf 'token: %s chars, sha256 %s\n' \
        "${#TOKEN}" "$(printf %s "$TOKEN" | shasum -a 256 | cut -c1-12)"
fi

say "2/6  put it on $NEW"
if [ "$SKIP_TOKEN" = 1 ]; then
    echo "skipping — already on the app"
else
    printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' "$TOKEN" | fly secrets import -a "$NEW" >/dev/null
    unset TOKEN
fi
fly secrets list -a "$NEW" | awk '$1=="CLAUDE_CODE_OAUTH_TOKEN"{print "  on the app:",$1}'

say "3/6  does the container resolve it?"
OUT=$(fly ssh console -a "$NEW" -C "/opt/hermes-v2/.venv/bin/python -c '$REMOTE_CHECK'" 2>&1 | tr -d '\r')
echo "$OUT" | grep -E 'RESOLVED|NONE' || fail "could not run the check in the container"
echo "$OUT" | grep -q RESOLVED || fail "the container still resolves no Anthropic token"

if [ "$TOKEN_ONLY" = 1 ]; then say "stopping here (--token-only)"; exit 0; fi

say "4/6  stop the old gateway"
# One bot token allows one long poller. The old one goes down before the new one
# comes up, or they fight and both lose (crew #15).
fly ssh console -a "$OLD" -C "supervisorctl stop gateway" 2>&1 | tail -2
fly ssh console -a "$OLD" -C "supervisorctl status gateway" 2>&1 | tail -1

say "5/6  start the new gateway"
# Delete the state file BEFORE the flip, and treat its absence as the starting
# point. The volume was restored from a backup of the founder's laptop, so
# /data/gateway_state.json arrived already populated — argv pointing at
# /Users/chidionyema/code/hermes-v2, hermes_home at ~/Documents/code/hermes-v2,
# written 2026-08-22T08:59Z by a process that has never run in this container.
# Polling that file for "connected" reads a laptop's history as if it were this
# machine's present. It happened to say "disconnected" so it would have timed
# out rather than lying, which is luck, not a design.
fly ssh console -a "$NEW" -C "/bin/sh -c 'rm -f /data/gateway_state.json'" >/dev/null 2>&1
echo "cleared the stale state file; the next one to appear is written by this boot"

# Setting a secret restarts the machine, and entrypoint.sh reads the flag on boot.
fly secrets set HERMES_GATEWAY_AUTOSTART=1 -a "$NEW" >/dev/null
echo "waiting for it to come up"
CONNECTED=0
for i in $(seq 1 20); do
    sleep 15
    RAW=$(fly ssh console -a "$NEW" \
        -C "/bin/sh -c 'cat /data/gateway_state.json 2>/dev/null || true'" 2>/dev/null \
        | tr -d '\r')
    # A state file naming a path that only exists on a Mac is not this container
    # reporting on itself. Refuse it rather than reading it.
    case "$RAW" in
        *'/Users/chidionyema'*)
            fail "the state file names a laptop path. Something restored it again; this is not a live reading." ;;
    esac
    STATE=$(printf %s "$RAW" \
        | grep -o '"state"[[:space:]]*:[[:space:]]*"[a-z]*"' | head -2 | tr '\n' ' ')
    printf '  %3ds  %s\n' "$((i*15))" "${STATE:-no state file yet}"
    case "$STATE" in *'"connected"'*) CONNECTED=1; break ;; esac
done

say "6/6  verdict"
if [ "$CONNECTED" = 1 ]; then
    echo "the new gateway is connected to Telegram."
    echo "send it a message. if it answers, the cutover is done and the next step is:"
    echo "    fly apps suspend $OLD          # keeps the volume; nothing is destroyed"
    exit 0
fi

printf '\033[31mthe new gateway did not connect. rolling back.\033[0m\n'
fly secrets set HERMES_GATEWAY_AUTOSTART=0 -a "$NEW" >/dev/null
fly ssh console -a "$OLD" -C "supervisorctl start gateway" 2>&1 | tail -2
fly ssh console -a "$OLD" -C "supervisorctl status gateway" 2>&1 | tail -1
echo "the old gateway is back. logs from the new one:"
fly ssh console -a "$NEW" -C "/bin/sh -c 'tail -40 /data/logs/gateway.log 2>/dev/null || true'" 2>&1 | tail -40
exit 1
