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

# import name -> the sibling *fork* that owns it, never a pip dependency here.
#
# ``model_tools`` is hermes-agent's own ``/app`` module (see
# ``patches/hermes-agent/``): it mounts in the same image it already ships and
# is git-ignored in a bare checkout, so it can never appear in
# ``otto/requirements.txt``. The gate exists to refuse an *undeclared real
# dependency*; a module a repository's own fork installs alongside it is not
# one. Carrying the fork name in the table (not a bare exemption) keeps the
# reason on the record for the next reader.
_FORK_OWNED_MODULES = {
    "model_tools": "hermes-agent (/app)",
    "lib": "chidionyema/idp@faa9fb7141d29296b2ff6ac44fd7535b7bb0e9c0 (image-pinned E1-E4 source)",
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


def _declared() -> set[str]:
    """Distribution names the manifest pins, plus fork-owned names the guard
    acknowledges instead of demanding a pip pin for."""
    declared = {re.split(r"[\[=]", line)[0].casefold() for line in _requirement_lines()}
    # A fork-owned module is declared by its owner, not by a pip line.
    declared |= {m.casefold() for m in _FORK_OWNED_MODULES}
    return declared


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
    declared = _declared()
    missing = []
    for module in sorted(_third_party_imports()):
        dist = _DIST_FOR_IMPORT.get(module, module)
        if dist.casefold() not in declared:
            owner = _FORK_OWNED_MODULES.get(module)
            note = f" (owned by {owner})" if owner else f" (distribution {dist})"
            missing.append(f"{module}{note}")
    assert missing == [], (
        f"otto/ imports these packages but otto/requirements.txt does not "
        f"pin them: {missing}"
    )


def test_idp_founder_verifier_is_pinned_to_exact_release_and_blobs() -> None:
    """An internal module is a real dependency: prove its supply chain pin."""
    docker = (OTTO_ROOT.parent / "Dockerfile").read_text()
    expected = (
        "ARG IDP_FOUNDER_PEP_SHA=faa9fb7141d29296b2ff6ac44fd7535b7bb0e9c0",
        'git -C /tmp/idp-founder-pep rev-parse HEAD',
        'git hash-object /tmp/idp-founder-pep/lib/founder_admission.py',
        '6700ed26034897e7bbbdc429ec5345ad6dea6238',
        'git hash-object /tmp/idp-founder-pep/lib/epistemic_kernel/kernel.py',
        'd24b91ed445eb0e1c7da712c7d074f82f6d0e113',
        'git hash-object /tmp/idp-founder-pep/lib/epistemic_kernel/__init__.py',
        '06e7249119de14d84ae3b3a5f26e783295cf53c5',
    )
    assert all(fragment in docker for fragment in expected), (
        "Missing immutable idp E1-E4 runtime dependency or exact blob checks"
    )
