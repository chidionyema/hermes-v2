"""crew#561: two defects the architect-doctor playbook read out of the cluster pod (oke-check run
33272111128, 2026-08-29).

1. `cp --no-preserve=ownership,mode` in deploy/k8s/entrypoint.sh dropped the exec bit on
   /data/bin/hermes, so every install-cron.py call died with "Permission denied" and no cron lane
   was ever installed. The copy keeps mode now, and the entrypoint refuses a build whose
   bin/hermes is not executable.
2. mac-run (idp platform/hermes-agent/mac-run.yaml) is `ssh` through `nc -x` and the image carried
   neither binary.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "deploy" / "k8s" / "entrypoint.sh"
DOCKERFILE = ROOT / "Dockerfile"


def test_the_build_copy_keeps_the_exec_bit():
    text = ENTRYPOINT.read_text()
    cp_lines = [ln for ln in text.splitlines() if ln.startswith("cp ")]
    assert cp_lines, "no cp line in the entrypoint"
    assert all(
        "no-preserve=ownership,mode" not in ln and "no-preserve=mode" not in ln
        for ln in cp_lines
    )
    assert all("--preserve=mode" not in ln for ln in cp_lines), (
        '--preserve=mode on "$BUILD"/. chmods the volume root the pod does not own '
        "(oke-check run 33283974599, 2026-08-30)"
    )
    assert re.search(r"(?m)^cp -R --no-preserve=ownership ", text)
    assert re.search(r'(?m)^chmod -R a\+X,u\+x "\$HERMES_HOME/bin"', text)
    assert text.index("chmod -R") > text.index("\ncp -R"), "chmod runs after the copy"
    assert 'test -x "$HERMES_HOME/bin/hermes"' in text


def test_the_image_carries_ssh_and_nc_for_mac_run():
    text = DOCKERFILE.read_text()
    assert "openssh-client" in text and "netcat-openbsd" in text


def test_bin_hermes_is_executable_in_git():
    assert (ROOT / "bin" / "hermes").stat().st_mode & 0o111


def test_chmod_after_the_copy_restores_the_exec_bit_on_a_file_the_volume_already_holds(
    tmp_path,
):
    """2026-08-29: the fix above shipped as plain `cp -R --no-preserve=ownership`, which keeps an
    existing destination's mode; the PVC already held bin/hermes at 0644, so `test -x` refused
    every boot (oke-check run 33281380053). --preserve=mode was tried next and chmods the volume
    root itself: "cp: preserving permissions for '/data/.': Operation not permitted" (oke-check run
    33283974599, 2026-08-30). A chmod on bin/ after the copy touches only what the build owns."""
    import shutil
    import subprocess

    cp = shutil.which("gcp") or shutil.which("cp")
    if subprocess.run([cp, "--version"], capture_output=True).returncode != 0:
        import pytest

        pytest.skip("no GNU cp on this machine")
    src, dst = tmp_path / "build", tmp_path / "data"
    (src / "bin").mkdir(parents=True)
    (dst / "bin").mkdir(parents=True)
    (src / "bin" / "hermes").write_text("#!/bin/sh\n")
    (src / "bin" / "hermes").chmod(0o755)
    (dst / "bin" / "hermes").write_text("#!/bin/sh\n")
    (dst / "bin" / "hermes").chmod(0o644)
    subprocess.run(
        [cp, "-R", "--no-preserve=ownership", f"{src}/.", f"{dst}/"], check=True
    )
    assert not (dst / "bin" / "hermes").stat().st_mode & 0o111, (
        "plain cp keeps the old 0644"
    )
    subprocess.run(["chmod", "-R", "a+X,u+x", str(dst / "bin")], check=True)
    assert (dst / "bin" / "hermes").stat().st_mode & 0o111
