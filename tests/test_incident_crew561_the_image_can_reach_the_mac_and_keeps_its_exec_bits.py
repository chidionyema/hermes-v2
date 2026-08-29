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
    assert (
        "--no-preserve=ownership,mode" not in text and "--no-preserve=mode" not in text
    )
    assert re.search(r"cp -R --no-preserve=ownership ", text)
    assert 'test -x "$HERMES_HOME/bin/hermes"' in text


def test_the_image_carries_ssh_and_nc_for_mac_run():
    text = DOCKERFILE.read_text()
    assert "openssh-client" in text and "netcat-openbsd" in text


def test_bin_hermes_is_executable_in_git():
    assert (ROOT / "bin" / "hermes").stat().st_mode & 0o111
