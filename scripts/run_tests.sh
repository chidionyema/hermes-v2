#!/bin/sh
# Verify command for this repo. Resolved by hermes-agent's detect_project_facts
# (scripts/run_tests.sh is its first-priority marker), so the verification
# ledger and the claim gate can back a DONE with a green run here.
# tests/ only: the vendored hermes-agent upstream suite is not this repo's gate.
cd "$(dirname "$0")/.." || exit 1
if [ -x .venv/bin/python ]; then
    exec .venv/bin/python -m pytest tests/ -q "$@"
fi
exec python3 -m pytest tests/ -q "$@"
