#!/usr/bin/env bash
# hermes cron refuses a symlink out of scripts/ ("escapes the scripts directory
# via traversal"), so scripts/ holds real copies. This keeps them honest.
set -euo pipefail
on_exit() {
	local ec=$?
	[ "$ec" -eq 0 ] || echo "  (exit $ec)" >&2
}
trap on_exit EXIT
H="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# an array, not a bare word list, so a second filename here stays a real
# loop instead of tripping shellcheck's single-iteration warning (SC2043)
FILES=(pulse.sh)
for f in "${FILES[@]}"; do
	cp "$H/bin/$f" "$H/scripts/$f" && chmod +x "$H/scripts/$f"
done
diff -q "$H/bin/pulse.sh" "$H/scripts/pulse.sh" && echo "scripts/ matches bin/"
