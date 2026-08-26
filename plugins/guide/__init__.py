"""guide: Otto teaches the founder what the Architect can do (LAW 32: a feature ships with an onboarding).

Founder, 2026-08-26: "Otto is the favourite and should teach me everything I need to know about
the Architect." The runtime swallows /start (gateway/run.py "Ignoring /start platform ping"), so
the first tap on the bot taught nothing, and /help lists runtime commands, not this estate.

The card is generated from what is on disk in $HERMES_HOME when he asks, so it cannot drift:
  skills/*/SKILL.md   frontmatter name + description -> what he can say and what happens
  cron/*.jobs         name + schedule + deliver       -> what runs on its own and where it reports
  plugin commands     the registry                    -> the slash commands
A skill that is deleted disappears from the card on the next /guide; nothing here is typed by hand.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

_FM = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path(__file__).resolve().parents[2])


def skills(home: Path) -> list[tuple[str, str]]:
    """The estate's own skills: skills/<name>/SKILL.md. Vendored categories (skills/<cat>/<name>/)
    are counted by `categories`, not listed one by one, so the card stays one screen."""
    out = []
    for f in sorted(home.glob("skills/*/SKILL.md")):
        m = _FM.match(f.read_text(encoding="utf-8", errors="replace"))
        if not m:
            continue
        fields = dict(re.findall(r"^(\w+):\s*(.*)$", m.group(1), re.M))
        name = fields.get("name", f.parent.name).strip()
        desc = fields.get("description", "").strip().strip('"')
        first = re.split(r"(?<=[.!?])\s", desc, 1)[0] if desc else "(no description)"
        out.append((name, first))
    return out


def categories(home: Path) -> list[tuple[str, int]]:
    out = []
    for d in sorted(p for p in home.glob("skills/*") if p.is_dir() and not (p / "SKILL.md").is_file()):
        n = len(list(d.glob("*/SKILL.md")))
        if n:
            out.append((d.name, n))
    return out


def jobs(home: Path) -> list[tuple[str, str, str]]:
    out = []
    for f in sorted(home.glob("cron/*.jobs")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                j = json.loads(line)
            except ValueError:
                continue
            out.append((j.get("name", "?"), j.get("schedule", "?"), j.get("deliver", "local")))
    return out


def commands() -> list[tuple[str, str]]:
    try:
        from hermes_cli.plugins import get_plugin_commands
        reg = get_plugin_commands()
    except Exception:  # noqa: BLE001 - outside the gateway there is no registry; the card says so
        return []
    return sorted((f"/{k}", (v.get("description") or "") if isinstance(v, dict) else "") for k, v in reg.items())


def card(home: Optional[Path] = None, topic: str = "") -> str:
    home = home or _home()
    sk, jb, cm = skills(home), jobs(home), commands()
    topic = topic.strip().lower()
    if topic:
        hit = [f for f in sorted(home.glob("skills/**/SKILL.md")) if topic in f.parent.name.lower()]
        hit = hit or [home / "skills" / n / "SKILL.md" for n, d in sk if topic in d.lower()]
        if hit:
            body = _FM.sub("", hit[0].read_text(encoding="utf-8", errors="replace"), 1).strip()
            return f"[Architect] guide: {hit[0].parent.name}\n\n{body[:3500]}"
        return f"[Architect] guide: nothing on disk matches {topic!r}. Try /guide with no argument."
    lines = ["[Architect] What you can do from this chat, read from disk just now.", ""]
    lines.append("SAY IT, AND THIS HAPPENS")
    for n, d in sk:
        lines.append(f"• {n}: {d}")
    cats = categories(home)
    if cats:
        lines.append("• also on hand, ask by name: " + ", ".join(f"{c} ({n})" for c, n in cats))
    lines.append("")
    lines.append("RUNS ON ITS OWN")
    for n, s, dl in jb:
        lines.append(f"• {n} — {s} — reports to {dl}")
    lines.append("")
    lines.append("SLASH COMMANDS")
    lines += [f"• {c} {d}".rstrip() for c, d in cm] or ["• (registry not loaded: run this inside the gateway)"]
    lines.append("")
    lines.append("Type /guide <word> for the full text of one skill, e.g. /guide idea.")
    return "\n".join(lines)


def guide(raw_args: str) -> Optional[str]:
    return card(topic=raw_args or "")


def register(ctx) -> None:
    ctx.register_command("guide", guide, description="What the Architect can do, read from disk now",
                         args_hint="[topic]")
