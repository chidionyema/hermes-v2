#!/usr/bin/env bash
# Encrypt the Claude credential to a file the repo can carry, so the container
# can decrypt it at boot with a key the platform holds.
#
#     ./scripts/bootstrap-age-auth.sh
#
# Two things this does differently from the obvious version, both of them the
# difference between encryption and theatre:
#
# 1. THE PRIVATE KEY NEVER ENTERS THE REPO. It is written to ~/.config/hermes/
#    and this script refuses to write it anywhere under the working tree.
#    Committing the key next to the ciphertext it opens is the same as
#    committing the plaintext, except it reads as safe. The key goes to the
#    platform as a secret, and to a password manager as the backup.
#
# 2. ON macOS THE SOURCE IS THE KEYCHAIN, NOT ~/.claude/.credentials.json.
#    That file is a stale leftover on this Mac — measured expiresAt 2026-08-05,
#    months dead. Encrypting it produces a perfectly valid ciphertext of a
#    credential that cannot authenticate, and the failure appears at boot on a
#    server rather than here.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
SECRETS_DIR="$REPO_ROOT/deploy/secrets"
OUT="$SECRETS_DIR/claude-credentials.json.age"
KEY_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/hermes"
KEY_FILE="$KEY_DIR/age-key.txt"
APP="${HERMES_AUTH_APP:-prospector-hermes-v2}"

die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

command -v age       >/dev/null 2>&1 || die "age is not installed. brew install age"
command -v age-keygen >/dev/null 2>&1 || die "age-keygen is not installed. brew install age"

case "$KEY_FILE" in
    "$REPO_ROOT"/*) die "the key path is inside the repo. That is the one place it must not be." ;;
esac

mkdir -p "$SECRETS_DIR" "$KEY_DIR"
chmod 700 "$KEY_DIR"

if [ ! -f "$KEY_FILE" ]; then
    echo "generating an age keypair at $KEY_FILE"
    age-keygen -o "$KEY_FILE" 2>/dev/null
    chmod 600 "$KEY_FILE"
else
    echo "using the existing key at $KEY_FILE"
fi
PUB=$(grep -i "public key" "$KEY_FILE" | sed 's/.*: *//' | tr -d ' \r')
[ -n "$PUB" ] || die "could not read the public key out of $KEY_FILE"
echo "public key: $PUB"

# ---------------------------------------------------------------- the source
TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT; chmod 600 "$TMP"

# HERMES_AUTH_SOURCE names a credential file that belongs to the SERVER, minted
# by `claude setup-token`. It is the supported way past the refusal below,
# because such a token has its own refresh lineage and local use cannot revoke
# it. Anything read from the keychain is this Mac's own and is marked as such.
if [ -n "${HERMES_AUTH_SOURCE:-}" ]; then
    echo "reading the credential named by HERMES_AUTH_SOURCE"
    [ -f "$HERMES_AUTH_SOURCE" ] || die "HERMES_AUTH_SOURCE is set but $HERMES_AUTH_SOURCE does not exist"
    cat "$HERMES_AUTH_SOURCE" > "$TMP"
elif [ "$(uname -s)" = "Darwin" ]; then
    echo "reading the live credential from the login keychain"
    SOURCE_IS_INTERACTIVE=1
    if ! security find-generic-password -s "Claude Code-credentials" -w > "$TMP" 2>/dev/null; then
        die "the keychain item 'Claude Code-credentials' would not read.
       If macOS refused it, approve the prompt and run this again.
       If it does not exist, run: claude setup-token"
    fi
else
    echo "reading ~/.claude/.credentials.json"
    [ -f "$HOME/.claude/.credentials.json" ] || die "no credential. Run: claude setup-token"
    cat "$HOME/.claude/.credentials.json" > "$TMP"
fi

# A credential that has already expired encrypts just as cleanly as a live one.
python3 - "$TMP" <<'PY' || die "the credential did not parse, or has no access token"
import json, sys, datetime
d = json.load(open(sys.argv[1]))
o = d.get("claudeAiOauth", d)
assert o.get("accessToken"), "no accessToken"
exp = o.get("expiresAt")
if exp:
    when = datetime.datetime.fromtimestamp(exp / 1000, datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    print(f"   expires {when.isoformat()} ({'EXPIRED' if when < now else 'live'})")
    if when < now and not o.get("refreshToken"):
        raise SystemExit("expired and no refresh token")
print("   refresh token:", "yes" if o.get("refreshToken") else "no")
PY

# ------------------------------------------------------------ the rotation
# MEASURED 2026-08-23. The blob this script wrote on 2026-08-22 at 14:48 was
# dead by 19:54 the same day, and Fly logged "OAuth access token has been
# revoked" on every job for the next 19 hours. The credential was live when it
# was encrypted. That is the trap: validating liveness here proves nothing.
#
# Claude Code rotates the refresh token on every refresh. Two clients holding
# one refresh token is one client too many -- whichever refreshes first gets a
# new pair and the other's copy is revoked by the server. The laptop refreshes
# constantly, so a copy of the laptop's credential survives on a server only
# until the next local refresh, roughly five hours.
#
# Proof, comparing the shipped blob with the keychain on 2026-08-23:
#   shipped  accessToken sha 2d35dfd04a15  refreshToken sha 302691eaf0b2  EXPIRED
#   live     accessToken sha 510a79797de3  refreshToken sha beb3b9319c1f  LIVE
# Different refresh tokens. The pair had rotated underneath the server.
#
# The server needs its OWN credential, from `claude setup-token`, which mints a
# long-lived token with its own refresh lineage that local use does not revoke.
if [ "${SOURCE_IS_INTERACTIVE:-0}" = "1" ] && [ "${HERMES_ALLOW_SHARED_CREDENTIAL:-0}" != "1" ]; then
    die "refusing to encrypt this Mac's interactive Claude Code credential.

       It is the same credential this laptop is using. Copying it to a server
       gives that server about five hours before a local refresh revokes it,
       and the failure appears as HTTP 401 in Fly logs, not here.

       Mint a credential that belongs to the server:

           claude setup-token

       then re-run this script with HERMES_AUTH_SOURCE=/path/to/that/credential.

       To ship the shared one anyway, knowing it dies within hours:

           HERMES_ALLOW_SHARED_CREDENTIAL=1 $0"
fi

# --------------------------------------------------------------- the encrypt
age -r "$PUB" "$TMP" > "$OUT"
chmod 644 "$OUT"
rm -f "$TMP"; trap - EXIT
echo "wrote $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes)"

cat <<EOF

Done. Two commands left, in this order:

  1. Put the decryption key on the platform. The key is read from the file by
     the shell, so it is never typed, echoed or left in shell history:

       fly secrets set AGE_PRIVATE_KEY="\$(grep -v 'public key' $KEY_FILE)" -a $APP

  2. Commit the ciphertext. Not the key — $KEY_FILE is outside the repo and
     .gitignore refuses it anyway:

       git add deploy/secrets/claude-credentials.json.age
       git commit -m "auth: age-encrypted Claude credential"

Back the key up somewhere that is not this Mac. It is the only thing that opens
the ciphertext, and losing it means running this script again from a browser.
EOF
