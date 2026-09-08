"""Regression: every otto dependency is declared, and declared pinned.

Independent verifier probe (crew#768 hardening wave): the otto packages
had no dependency manifest at all, so nothing named the versions the
suite was proved against — the class of defect is an unpinned name that
installs a different version tomorrow. ``otto/requirements.txt`` now
declares them; this guard fails on any unpinned line and on any
third-party import in ``otto/`` the manifest does not carry.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

OTTO_ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = OTTO_ROOT / "requirements.txt"

# One exact pin per line: name (optional extras) == version. No ranges,
# no bare names, no environment markers hiding an unpinned fallback.
_PIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?==\S+$")

# import name -> distribution name, where they differ
_DIST_FOR_IMPORT = {
    "nats": "nats-py",
    "ulid": "python-ulid",
    "yaml": "PyYAML",
    "pytest_bdd": "pytest-bdd",
    "opentelemetry": "opentelemetry-sdk",
}


def _requirement_lines() -> list[str]:
    lines = []
    for raw in REQUIREMENTS.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def _third_party_imports() -> set[str]:
    stdlib = set(sys.stdlib_module_names)
    found: set[str] = set()
    for path in OTTO_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add(node.module.split(".")[0])
    return {m for m in found if m not in stdlib and m != "otto"}


def test_requirements_file_exists() -> None:
    assert REQUIREMENTS.is_file(), (
        "otto/requirements.txt is the dependency manifest for the otto "
        "packages and must exist"
    )


def test_every_requirement_line_is_an_exact_pin() -> None:
    unpinned = [line for line in _requirement_lines() if not _PIN.match(line)]
    assert unpinned == [], (
        f"unpinned or malformed requirement lines: {unpinned}; every line "
        "must be name==exact.version"
    )


def test_every_third_party_import_is_declared() -> None:
    declared = {re.split(r"[\[=]", line)[0].casefold() for line in _requirement_lines()}
    missing = []
    for module in sorted(_third_party_imports()):
        dist = _DIST_FOR_IMPORT.get(module, module)
        if dist.casefold() not in declared:
            missing.append(f"{module} (distribution {dist})")
    assert missing == [], (
        f"otto/ imports these packages but otto/requirements.txt does not "
        f"pin them: {missing}"
    )
