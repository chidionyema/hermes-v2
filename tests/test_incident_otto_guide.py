"""Otto teaches from disk, not from a list someone typed (founder 2026-08-26, LAW 32).

Rung 4, incident: the first tap on the bot (/start) taught nothing and /help named runtime
commands, not the estate. The rule: every skill and every scheduled job on disk is on the card,
and one that is removed leaves the card without anyone editing prose.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("guide", ROOT / "plugins" / "guide" / "__init__.py")
guide = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guide)  # type: ignore[union-attr]


def _estate(tmp_path, skills, jobs):
    for name, desc in skills:
        d = tmp_path / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n\nBody of {name}.\n")
    (tmp_path / "cron").mkdir()
    (tmp_path / "cron" / "work.jobs").write_text("# comment\n" + "\n".join(json.dumps(j) for j in jobs) + "\n")
    return tmp_path


def test_incident_otto_guide_names_every_skill_and_job_on_disk_and_forgets_the_removed_one(tmp_path):
    home = _estate(tmp_path,
                   [("phone-idea-flow", "An idea from the phone becomes a card only after you choose. Second sentence."),
                    ("estate-map", "Print the shape of the estate.")],
                   [{"name": "work-agent-go", "schedule": "*/20 * * * *", "deliver": "telegram"}])
    text = guide.card(home)
    assert "• phone-idea-flow: An idea from the phone becomes a card only after you choose." in text
    assert "Second sentence" not in text, "the card shows one sentence per skill"
    assert "• estate-map:" in text and "• work-agent-go — */20 * * * * — reports to telegram" in text
    assert text.startswith("[Architect]"), "every message is labelled (SOUL rule)"
    # Remove a skill: it leaves the card with no prose edit anywhere.
    (home / "skills" / "estate-map" / "SKILL.md").unlink()
    assert "estate-map" not in guide.card(home)


def test_guide_topic_returns_the_skill_body_and_says_so_when_nothing_matches(tmp_path):
    home = _estate(tmp_path, [("phone-idea-flow", "Ideas from the phone.")], [])
    assert "Body of phone-idea-flow." in guide.card(home, "idea")
    assert "nothing on disk matches" in guide.card(home, "unicorn")


def test_guide_reads_the_real_tree():
    text = guide.card(ROOT.parent / "hermes-v2") if (ROOT.parent / "hermes-v2" / "skills").is_dir() else guide.card(ROOT)
    assert "SAY IT, AND THIS HAPPENS" in text and "RUNS ON ITS OWN" in text
