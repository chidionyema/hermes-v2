#!/usr/bin/env bash
# Canonical test runner for the otto/ packages (crew#768, the Otto spine).
#
# Run this instead of calling `pytest otto/tests/...` directly, for exactly
# the same reason hermes-agent's scripts/run_tests.sh exists: the test command
# is a *contract*, not folklore. The dependency truth lives in one place —
# otto/requirements.txt — and this runner consumes it; an agent never hand-types
# a `--with psycopg-binary` list, because that list has been wrong twice and
# will be wrong again as long as two copies of it exist.
#
# Three things a green run must prove, none of which "exit 0" proves on its own:
#   1. the dependencies the suite imports are actually installed here
#      (a missing dep fails loudly, it is never import-dodged or worked around
#      by making a production module import lazily);
#   2. tests were *collected* — a zero-count run is a failure (`5`/`4`), never a pass;
#   3. the count is printed, so "green" always means "N passed", never silence.
#
# The interpreter is the same one the rest of the repo uses: a .venv if present,
# else the default python3. Deps are pinned in otto/requirements.txt (the
# guard otto/tests/integration/test_requirements_pinned.py fails on any unpinned
# line or any third-party import not declared there).
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

fail_loud() { printf '\n\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

# 1. The dependency manifest exists — without it there is no contract to consume.
[ -f otto/requirements.txt ] || fail_loud "otto/requirements.txt is missing; the otto dependency contract does not exist"

# 2. The interpreter. Prefer a .venv (the repo's own bin/verify uses it), then
#    a versioned python3.12+, and only then a bare python3 — which must still be
#    3.12+ to run this suite (the pin guard uses sys.stdlib_module_names, and
#    otto pins against the 3.12 line). The estuary's own macOS `python3` is 3.9
#    and silently naming it acceptable is the exact class of silent wrongness
#    this runner exists to refuse. A stale `.venv` whose base interpreter was
#    deleted is a dangling symlink, not a live environment: every candidate must
#    actually *execute*, not merely exist with an exec bit.
py_runs() { "$1" --version >/dev/null 2>&1; }
PY=""
if [ -x .venv/bin/python ] && py_runs .venv/bin/python; then
	PY=.venv/bin/python
elif command -v python3.12 >/dev/null 2>&1 && py_runs python3.12; then
	PY=python3.12
elif command -v python3 >/dev/null 2>&1 && py_runs python3; then
	PY=python3
else
	fail_loud "no *working* Python interpreter found; a stale .venv points at a deleted base Python, and python3.12 is absent or broken. Install python3.12 (or recreate .venv with `uv venv`) and re-run."
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
	fail_loud "'$PY' is $("$PY" --version 2>&1) — too old. This suite needs Python >= 3.10 (the pin guard uses sys.stdlib_module_names); set up .venv or a python3.12+ and re-run."
fi

# 3. Every dependency the code being run imports is importable by this
#    interpreter, or we stop now with the named remediation. This closes the
#    class of "the test hit the executor ceiling while rediscovering a missing
#    dep": the dep check is the first thing that runs, in milliseconds, not the
#    last retry after a timeout. The import->dist mapping and the fork-owned
#    exemptions come from otto/tests/integration/test_requirements_pinned.py —
#    the one place that already owns them — so this runner can never drift
#    into a second copy. The scope is the paths this run will load (the given
#    targets, or the whole otto/tests/ tree when none are given), so a scoped
#    run checks scoped deps and does not falsely demand cryptography/asyncpg
#    for a suite that never imports them.
deptargets=()
if [ "$#" -gt 0 ]; then deptargets=("$@"); else deptargets=(otto/tests/); fi
import_check="$("$PY" - "${deptargets[@]}" <<'PY'
import ast, importlib, pathlib, sys
sys.path.insert(0, "otto/tests/integration")
from test_requirements_pinned import _DIST_FOR_IMPORT, _FORK_OWNED_MODULES

roots = [pathlib.Path(p) for p in sys.argv[1:]]
stdlib = set(sys.stdlib_module_names)
files: set[pathlib.Path] = set()
for root in roots:
    if root.is_file() and root.suffix == ".py":
        files.add(root)
    elif root.is_dir():
        files.update(root.rglob("*.py"))
# a conftest.py at any ancestor of the targets is loaded by pytest too.
for f in list(files):
    for parent in f.parents:
        c = parent / "conftest.py"
        if c.is_file():
            files.add(c)

found: set[str] = set()
for path in files:
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
skip = stdlib | {"otto"} | set(_FORK_OWNED_MODULES)
missing = []
for mod in sorted(found - skip):
    try:
        importlib.import_module(mod)
    except Exception:
        missing.append(f"{_DIST_FOR_IMPORT.get(mod, mod)} (import {mod})")
if missing:
    print("MISSING DEPS: " + ", ".join(missing))
    sys.exit(1)
PY
)" || fail_loud "Declared dependencies for the targets are not installed for '$PY'.\n${import_check}\nRemediate: install the pinned deps for the interpreter this repo used to prove the suite, e.g.\n  uv pip install --python '$PY' -r otto/requirements.txt\nand re-run this script."

# 4. Run the suite with a collected-test count as the verdict. With no explicit
#    targets, the whole otto/tests/ tree runs; with targets, only those run, so a
#    scoped run is genuinely scoped. `-p no:cacheprovider` keeps a stale cache
#    from masking a zero-collect as "0 failed".
if [ "$#" -gt 0 ]; then
	exec "$PY" -m pytest "$@" -p no:cacheprovider --no-header -rA
fi
exec "$PY" -m pytest otto/tests/ -p no:cacheprovider --no-header -rA
