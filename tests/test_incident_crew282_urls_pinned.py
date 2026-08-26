"""crew#282 (rung 4): every UI URL pinned in Telegram from the catalogue.
Both ways: an https link on the estate's host is listed, a localhost link and a
Resource are not; the first tick sends and pins, the same card again is silent,
a changed card edits the pinned message instead of posting a second one."""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "estate-urls.py"
CATALOG = """apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: catalogue
  title: Backstage
  links:
    - url: https://catalogue.example.com/
      title: open
    - url: http://127.0.0.1:3000
      title: local
---
kind: Resource
metadata:
  name: ledger
  links:
    - url: https://ledger.example.com/
---
kind: Component
metadata:
  name: dagster
  links:
    - url: https://dagster.example.com/
"""


def run(tmp, catalog, *args):
    (tmp / "estate.yaml").write_text("urls:\n  catalog_repo: x/idp\n  include: example.com\n")
    (tmp / "catalog.yaml").write_text(catalog)
    env = dict(os.environ, HERMES_ESTATE_YAML=str(tmp / "estate.yaml"), HERMES_URLS_STATE=str(tmp / "pin.json"),
               TELEGRAM_HOME_CHANNEL="-100")
    return subprocess.run([sys.executable, str(SCRIPT), "--catalog", str(tmp / "catalog.yaml"), "--transport",
                           str(tmp / "calls.jsonl"), *args], capture_output=True, text=True, env=env)


def calls(tmp):
    p = tmp / "calls.jsonl"
    return [json.loads(l)["method"] for l in p.read_text().splitlines()] if p.exists() else []


def test_incident_crew282_card_lists_estate_https_links_only(tmp_path):
    r = run(tmp_path, CATALOG, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "https://catalogue.example.com/" in r.stdout and "https://dagster.example.com/" in r.stdout
    assert "127.0.0.1" not in r.stdout and "ledger" not in r.stdout and "2 links" in r.stdout


def test_incident_crew282_first_tick_pins_then_silent_then_edits(tmp_path):
    r1 = run(tmp_path, CATALOG)
    assert r1.returncode == 0 and r1.stdout.startswith("URLS pinned msg=1 n=2"), r1.stdout + r1.stderr
    assert calls(tmp_path) == ["sendMessage", "pinChatMessage"]
    r2 = run(tmp_path, CATALOG)
    assert r2.stdout == "" and calls(tmp_path) == ["sendMessage", "pinChatMessage"]
    r3 = run(tmp_path, CATALOG.replace("dagster.example.com", "dagster2.example.com"))
    assert r3.stdout.startswith("URLS pinned msg=1") and calls(tmp_path)[-1] == "editMessageText"
    assert calls(tmp_path).count("sendMessage") == 1
