"""crew#620 CP3: bin/shell-strict must refuse every one of the four rules
individually (shellcheck, shfmt, strict mode, trap) and must pass a clean
file. The workflow must actually run the gate it names."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[1] / "bin" / "shell-strict"
ROOT = GATE.parents[1]
GATES_YML = ROOT / ".github" / "workflows" / "gates.yml"

HAS_SHELLCHECK = shutil.which("shellcheck") is not None
HAS_SHFMT = (
    shutil.which("shfmt") is not None or (Path.home() / "go" / "bin" / "shfmt").exists()
)

# shfmt's default indent is a tab, not two spaces -- these fixtures use a real
# tab (\t) inside the function bodies so a *correctly formatted* file is the
# one that passes, and the deliberately-misindented one below is the one that
# doesn't.
CLEAN = (
    "#!/usr/bin/env bash\n"
    "set -euo pipefail\n"
    "\n"
    "on_exit() {\n"
    "\tlocal ec=$?\n"
    '\t[ "$ec" -eq 0 ] || echo "  (exit $ec)" >&2\n'
    "}\n"
    "trap on_exit EXIT\n"
    "\n"
    'echo "hello"\n'
)

NO_DASH_E = CLEAN.replace("set -euo pipefail\n", "set -uo pipefail\n")


def run(root):
    env = os.environ.copy()
    go_bin = str(Path.home() / "go" / "bin")
    if go_bin not in env.get("PATH", ""):
        env["PATH"] = env.get("PATH", "") + ":" + go_bin
    return subprocess.run(
        [sys.executable, str(GATE), str(root)],
        capture_output=True,
        text=True,
        env=env,
    )


def write(root, name, text):
    f = root / name
    f.write_text(text)
    f.chmod(0o755)
    return f


@pytest.mark.skipif(
    not (HAS_SHELLCHECK and HAS_SHFMT), reason="shellcheck/shfmt not on this machine"
)
def test_clean_file_passes(tmp_path):
    write(tmp_path, "ok.sh", CLEAN)
    r = run(tmp_path)
    assert r.returncode == 0, r.stdout
    assert r.stdout == ""


@pytest.mark.skipif(
    not (HAS_SHELLCHECK and HAS_SHFMT), reason="shellcheck/shfmt not on this machine"
)
def test_missing_strict_mode_is_refused(tmp_path):
    text = CLEAN.replace("set -euo pipefail\n", "")
    write(tmp_path, "nostrict.sh", text)
    r = run(tmp_path)
    assert r.returncode == 1
    assert "nostrict.sh: missing set -euo pipefail" in r.stdout, r.stdout


@pytest.mark.skipif(
    not (HAS_SHELLCHECK and HAS_SHFMT), reason="shellcheck/shfmt not on this machine"
)
def test_missing_trap_is_refused(tmp_path):
    text = '#!/usr/bin/env bash\nset -euo pipefail\n\necho "hello"\n'
    write(tmp_path, "notrap.sh", text)
    r = run(tmp_path)
    assert r.returncode == 1
    assert "notrap.sh: missing a trap" in r.stdout, r.stdout


@pytest.mark.skipif(not HAS_SHELLCHECK, reason="shellcheck not on this machine")
def test_shellcheck_warning_is_refused(tmp_path):
    # SC2046: unquoted $(pwd) risks word-splitting -- a real shellcheck -S
    # warning finding (SC2164's cd-without-||-exit is suppressed once -e is
    # active, so it can't be used here), everything else stays identical to
    # the CLEAN fixture.
    text = CLEAN.replace('echo "hello"\n', "ls $(pwd)\n")
    write(tmp_path, "sc.sh", text)
    r = run(tmp_path)
    assert r.returncode == 1
    assert "sc.sh: shellcheck -S warning is not clean" in r.stdout, r.stdout


@pytest.mark.skipif(not HAS_SHFMT, reason="shfmt not on this machine")
def test_shfmt_diff_is_refused(tmp_path):
    # Same file, but the function body uses 4 spaces instead of the tab shfmt
    # wants -- shellcheck stays clean, only formatting is wrong.
    text = CLEAN.replace("\tlocal ec=$?\n", "    local ec=$?\n").replace(
        '\t[ "$ec" -eq 0 ]', '    [ "$ec" -eq 0 ]'
    )
    write(tmp_path, "fmt.sh", text)
    r = run(tmp_path)
    assert r.returncode == 1
    assert "fmt.sh: shfmt -d is not clean" in r.stdout, r.stdout


@pytest.mark.skipif(
    not (HAS_SHELLCHECK and HAS_SHFMT), reason="shellcheck/shfmt not on this machine"
)
def test_verify_and_verify_consult_are_exempt_from_dash_e_only(tmp_path):
    """The exemption is by exact relative path, not a blanket free pass: it waives
    only rule 3's -e half, and only for these two names (bin/verify,
    bin/verify-consult) -- both are pass/fail report harnesses that must keep
    running past a FAIL, so -e would truncate their report at the first red."""
    (tmp_path / "bin").mkdir()
    write(tmp_path / "bin", "verify", NO_DASH_E)
    r = run(tmp_path)
    assert r.returncode == 0, r.stdout


@pytest.mark.skipif(
    not (HAS_SHELLCHECK and HAS_SHFMT), reason="shellcheck/shfmt not on this machine"
)
def test_a_different_file_without_dash_e_is_not_exempt(tmp_path):
    """Same set -uo pipefail body, different path -- the exemption is by name,
    not by content, so this one must still be refused."""
    (tmp_path / "bin").mkdir()
    write(tmp_path / "bin", "not-verify.sh", NO_DASH_E)
    r = run(tmp_path)
    assert r.returncode == 1
    assert "missing set -euo pipefail" in r.stdout, r.stdout


def test_gates_workflow_names_the_shell_strict_job():
    text = GATES_YML.read_text()
    assert "shell-strict" in text, text
