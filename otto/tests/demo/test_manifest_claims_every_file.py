"""The real manifest, run against the real repository (crew#768/#774).

``bin/otto-demo``'s own step_defs suite (``step_defs/test_w3_demo_command.py``)
proves the sweep mechanism works, but only against small fixture manifests it
writes itself -- it never runs the sweep against the shipped
``DEFAULT_MANIFEST`` and the tests that actually exist on disk. That gap is
exactly how ``otto/tests/ingress/`` and ``otto/tests/tenancy/`` landed on
``main`` (PR #67) with no section claiming them: nothing failed until the CI
job that runs ``bin/otto-demo`` for real (crew#774) caught it by hand.

This test closes that gap: it imports the live command (a script with no
``.py`` suffix, so ``importlib`` loads it by path rather than a normal
import) and runs its real ``sweep_for_unclaimed_tests`` against its real
``DEFAULT_MANIFEST``. Revert the manifest fix and this fails again -- it does
not merely re-describe the fix.
"""

from __future__ import annotations

import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_COMMAND = REPO_ROOT / "bin" / "otto-demo"


def _load_otto_demo():
    # bin/otto-demo carries no .py suffix (it is invoked directly, not
    # imported), so importlib cannot infer a loader from the extension --
    # an explicit SourceFileLoader is required.
    loader = SourceFileLoader("otto_demo_under_test", str(DEMO_COMMAND))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None, "could not build a module spec for bin/otto-demo"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    loader.exec_module(module)
    return module


def test_every_collected_test_file_is_claimed_by_a_manifest_section() -> None:
    otto_demo = _load_otto_demo()
    unclaimed = otto_demo.sweep_for_unclaimed_tests(otto_demo.DEFAULT_MANIFEST)
    assert unclaimed == [], (
        "bin/otto-demo's DEFAULT_MANIFEST does not claim: "
        + ", ".join(row.path for row in unclaimed)
        + " -- add a section for it in DEFAULT_MANIFEST"
    )


def test_every_manifest_section_path_exists() -> None:
    otto_demo = _load_otto_demo()
    missing = [
        entry["path"]
        for entry in otto_demo.DEFAULT_MANIFEST["sections"]
        if not (REPO_ROOT / entry["path"]).exists()
    ]
    assert missing == [], f"manifest section path(s) do not exist on disk: {missing}"
