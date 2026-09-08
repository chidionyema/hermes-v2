#!/usr/bin/env bash
# Silent unless something is genuinely waiting on the founder.
# Reads the one real register: ~/.claude/scripts/founder_actions.py
# no_agent cron job: this script's stdout IS the message, or nothing is sent at all.
set -euo pipefail

REGISTER=~/.claude/scripts/founder_actions.py
[ -f "$REGISTER" ] || exit 0   # register missing -> nothing to report, stay silent, not an error

python3 "$REGISTER" --json 2>/dev/null | python3 -c "
import json, sys
try:
    items = (lambda d: d['open'] + d['unverifiable'])(json.load(sys.stdin))
except Exception:
    sys.exit(0)   # register broken/empty -> stay silent, never crash a no_agent job
if not items:
    sys.exit(0)   # nothing waiting -> no output -> no_agent job sends nothing
print(f'\U0001F534 {len(items)} thing(s) waiting on you \u2014 nothing moves on these until you act:\n')
for i in items:
    print(f\"\u2022 {i['what']}\")
    print(f\"  why: {i['why_founder']}\")
    print(f\"  unblocks: {i['unblocks']}\n\")
" || true
