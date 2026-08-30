"""crew#717 wave 1: Otto is seen (Langfuse traces, LAW 50) and speaks (edge-tts in the image).

Founder, 2026-08-30: "i need otto sorted once and for all". Wave 1 is the powers that need no
new vendor key: the observability plugin is enabled in config (it gates itself on credentials,
so a laptop run without keys is unchanged), its SDK and the voice extra actually ship in the
image, and the idp otto-parity drill grades both from inside the pod.
"""

from __future__ import annotations

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_incident_crew717_langfuse_plugin_is_enabled_and_its_sdk_ships():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert "observability/langfuse" in cfg["plugins"]["enabled"]
    # The plugin itself lives in the pinned hermes-agent fork the Dockerfile clones, not here.
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "langfuse==" in docker, "the plugin's SDK is not in the image"


def test_incident_crew717_the_voice_extra_ships_in_the_image():
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--extra edge-tts" in docker, "edge-tts is not installed into the image"
