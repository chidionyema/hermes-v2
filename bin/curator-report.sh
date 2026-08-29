#!/usr/bin/env bash
# Weekly. Ask the curator what it thinks of the skills, write it down, act on
# nothing. The report is an input to the Sunday review, not an instruction.
set -euo pipefail
on_exit() {
	local ec=$?
	[ "$ec" -eq 0 ] || echo "  (exit $ec)" >&2
}
trap on_exit EXIT
H="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$H"
mkdir -p logs/curator
{
	echo "# Curator report - $(date -u +%Y-%m-%dT%H:%MZ)"
	echo
	echo "## Status"
	bin/hermes curator status 2>&1
	echo
	echo "## Skill usage, with provenance"
	bin/hermes curator usage 2>&1
	echo
	echo "## What the Sunday review does with this"
	echo "Nothing automatically. A skill only changes through a PR the founder taps."
} >logs/curator/REPORT.md
echo "wrote logs/curator/REPORT.md"
