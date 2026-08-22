#!/usr/bin/env bash
# hermes cron refuses a symlink out of scripts/ ("escapes the scripts directory
# via traversal"), so scripts/ holds real copies. This keeps them honest.
set -euo pipefail
H="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for f in pulse.sh; do
  cp "$H/bin/$f" "$H/scripts/$f" && chmod +x "$H/scripts/$f"
done
diff -q "$H/bin/pulse.sh" "$H/scripts/pulse.sh" && echo "scripts/ matches bin/"
