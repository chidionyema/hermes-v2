#!/usr/bin/env bash
# Write the cost files from measured usage, never from an estimate.
# Spec §10.1. A cost claim with no number behind it is worth nothing.
set -euo pipefail
on_exit() {
	local ec=$?
	[ "$ec" -eq 0 ] || echo "  (exit $ec)" >&2
}
trap on_exit EXIT
H="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$H"
mkdir -p logs/cost

# Hermes keeps its own usage accounting. This asks it, rather than guessing.
{
	echo "# Measured $(date -u +%Y-%m-%dT%H:%MZ) by bin/cost-report.sh"
	echo "# Source: hermes insights usage. Nothing here is estimated."
	echo
	bin/hermes insights --days 30 2>&1 || bin/hermes curator usage 2>&1 ||
		echo "NOT MEASURABLE: hermes has no usage rows yet. This file stays honest and empty until it does."
} >logs/cost/watch-monthly.txt

{
	echo "# Week 1 spend, cross-checked against the provider's own /usage page."
	echo "# Two angles: hermes' accounting and the provider's bill. If they"
	echo "# disagree, the provider is right and hermes' accounting is the bug."
	echo
	echo "## Angle 1 - hermes"
	bin/hermes insights --days 7 2>&1 || echo "no rows yet"
	echo
	echo "## Angle 2 - provider"
	echo "Paste the figure from https://openrouter.ai/settings/credits and"
	echo "https://console.anthropic.com/settings/usage here, with the date."
	echo "UNFILLED as of $(date -u +%Y-%m-%d) - week 1 has not elapsed."
} >logs/cost/week1-usage.txt

if [ -f logs/cost/evolution.txt ]; then :; else
	{
		echo "# Cost per self-evolution run. One line per run, appended by cron/evolution.jobs."
		echo "# Upstream README states \$2-10 per optimization run. That number is theirs,"
		echo "# not ours, and does not count until a run of ours is measured here."
		echo "# date  skill  usd  outcome"
	} >logs/cost/evolution.txt
fi
echo "cost files written from measured data"
