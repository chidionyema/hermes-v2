"""crew#561, 2026-08-30 -- "OTTO CLAIMS NO ACCESS TO GITHUB OR MAC".

The parity playbook proved the pod could ssh the Mac; the agent inside Telegram still said it had
no access, because (a) every estate skill says `gh ...` and the image had no `gh`, and (b) no
skill told it `mac-run` exists. Both stay pinned here: the Dockerfile installs gh, the skill that
names mac-run ships, and no skill names a tool the image does not carry (fly is gone, R1).
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_installs_gh():
    text = (ROOT / "Dockerfile").read_text()
    assert "cli/cli/releases/download" in text and "gh --version" in text


def test_a_skill_names_mac_run():
    skill = ROOT / "templates" / "skills" / "founder-mac" / "SKILL.md.tmpl"
    assert skill.exists()
    assert "mac-run hostname" in skill.read_text()


def test_no_skill_or_approval_names_a_tool_the_image_lacks():
    # R1: fly is dead; a skill or approval row naming it teaches the agent a door that is bricked.
    offenders = []
    for p in [ROOT / "config.yaml", *ROOT.glob("templates/skills/*/SKILL.md.tmpl")]:
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if re.search(r"(^|\s)fly (deploy|scale|status|machine|logs)", line):
                offenders.append(f"{p.relative_to(ROOT)}:{n}")
    assert not offenders, offenders
