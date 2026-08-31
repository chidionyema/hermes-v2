"""crew#736 CP2: the boot contract exists and cannot rot.

Two images shipped green while unable to run: `anthropic` missing for 15 hours behind a
1/1 Ready pod, and a python interpreter under /root that uid 10001 could not exec.
Both were import/boot failures a build gate never exercised -- "Compiling is not
executing" (Unbreakable Release Contract G2, founder 2026-08-31). These pins keep the
contract wired: the workflow must run it, it must run secretless as the pod's uid, and
every extra the Dockerfile installs must be proven importable by boot-contract.txt.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "deploy/k8s/boot-contract.txt"
SCRIPT = ROOT / "deploy/k8s/boot-contract.sh"
WORKFLOW = ROOT / ".github/workflows/build-agent-image.yml"

# An extra on the Dockerfile's `uv sync` line -> a module boot-contract.txt must import.
# An extra this table does not know fails the test rather than passing quietly -- the
# same philosophy as PROVIDER_EXTRA in test_incident_crew516_cp4_image_carries_the_estate.
EXTRA_MODULE = {
    "messaging": "telegram.ext",
    "anthropic": "anthropic",
    "hindsight": "hindsight_client",
    "otlp": "opentelemetry.exporter.otlp.proto.http",
    "edge-tts": "edge_tts",
}


def contract_modules():
    mods = []
    for ln in CONTRACT.read_text().splitlines():
        ln = ln.split("#", 1)[0].strip()
        if ln:
            mods.append(ln)
    return mods


def test_every_installed_extra_is_proven_importable():
    m = re.search(r"^RUN uv sync .*$", (ROOT / "Dockerfile").read_text(), re.M)
    assert m, "Dockerfile lost its uv sync line"
    extras = re.findall(r"--extra (\S+)", m.group(0))
    assert extras, "uv sync installs no extras; the contract table is stale"
    mods = contract_modules()
    for extra in extras:
        assert extra in EXTRA_MODULE, (
            f"Dockerfile installs extra {extra!r} and EXTRA_MODULE does not know it: "
            "add it with the module the contract should import"
        )
        assert EXTRA_MODULE[extra] in mods, (
            f"extra {extra!r} installs but boot-contract.txt never imports "
            f"{EXTRA_MODULE[extra]!r} -- the next silent ImportError ships"
        )
    assert "hermes_cli.main" in mods, "the entrypoint module itself is not contracted"


def test_workflow_runs_the_contract_on_the_built_tar():
    wf = WORKFLOW.read_text()
    assert "docker load -i /tmp/scan.tar" in wf, (
        "the workflow never loads the tar it built"
    )
    assert "deploy/k8s/boot-contract.sh" in wf, (
        "the workflow never runs the boot contract"
    )
    # the contract must gate the push: it runs unconditionally, before sign-and-push steps
    assert wf.index("boot-contract.sh") < wf.index("sign the image"), (
        "the contract runs after signing; an unbootable image would already be pushed"
    )


def test_contract_is_secretless_and_runs_as_the_pods_uid():
    sh = SCRIPT.read_text()
    assert "--user 10001:10001" in sh, "the contract does not run as the pod's uid"
    assert "--read-only" in sh, (
        "the pod's root filesystem is read-only; the contract's is not"
    )
    assert "agent-card.json" in sh, "the contract never asks for the agent card"
    env_flags = re.findall(r"--env (\S+)", sh)
    assert env_flags == ["HOME=/tmp"], (
        f"the contract passes env {env_flags}; a secret smuggled here hides a boot "
        "that dies on the cluster while ESO is still syncing"
    )
