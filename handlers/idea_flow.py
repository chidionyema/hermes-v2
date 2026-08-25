#!/usr/bin/env python3
"""idea_flow: the deterministic half of the phone idea flow (crew#182, spec CP3-CP6, CP9-CP11).

The Architect (hermes-v2 on Telegram) runs this from the skill `phone-idea-flow`. Everything a
model could get wrong is here, where a test can hold it: which mode a message is in, whether
the idea already exists, what the draft looks like, and above all that no issue is written
until the founder has chosen a destination on a confirmation prompt the flow itself issued.

    idea_flow.py classify  "<message>"                 -> {"mode": "explore"|"build", ...}
    idea_flow.py dedup     "<title>"                   -> {"matches": [...]}  (issues, PRs, worktrees)
    idea_flow.py draft     "<title>" ["<message>"]     -> {"nonce": ..., "feature": ..., "prompt": ...}
    idea_flow.py create    --nonce N --choice todo|icebox|drop [--urgent] -> {"issue": n, "url": ...}
    idea_flow.py activate  "<icebox issue title or number>" -> draft again from the RFC (CP9)

The board is GitHub issues in chidionyema/crew (crew#182 v3, #188). Columns are labels:
`agent-go` is To Do, `icebox` is Icebox, `urgent` is a flag. Drop writes nothing. The dispatcher
(work-agent-go) reads only `agent-go`, so an icebox issue is never claimed (CP6).

CP5, the gate: `draft` writes a nonce to $HERMES_HOME/state/idea-flow/<nonce>.json with the draft
in it; `create` refuses without a nonce that exists and has not been used. The model cannot reach
the board without first having shown the founder the draft and the prompt.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

REPO = os.environ.get("IDEA_FLOW_REPO", "chidionyema/crew")
HOME = Path(os.environ.get("HERMES_HOME") or Path(__file__).resolve().parents[1])
STATE = HOME / "state" / "idea-flow"
CHOICES = ("To Do", "Icebox", "Drop")
LABEL = {"todo": "agent-go", "icebox": "icebox"}

EXPLORE = re.compile(
    r"\b(what if|brainstorm|just exploring|i'?m exploring|thinking out loud|thinking aloud|"
    r"sounding board|not sure yet|idea:|would it make sense|hypothetically|wondering (if|whether))\b",
    re.I,
)
BUILD = re.compile(
    r"\b(build|write a spec|write the spec|spec this|ship|implement|create|make it so|"
    r"add (a|the|an)|let'?s do it|do it|go ahead|remember that idea)\b",
    re.I,
)
URGENT = re.compile(r"\b(urgent|drop everything|asap|right now|p1|emergency)\b", re.I)


def classify(message: str) -> dict:
    """Exploring wins over building: a message that says 'what if we build X' is exploring.

    That order is the founder's rule (spec CP4): exploratory phrasing creates nothing, and the
    cost of a wrong 'explore' is one more message, while a wrong 'build' is a card he never
    asked for.
    """
    explore = bool(EXPLORE.search(message))
    build = bool(BUILD.search(message))
    mode = "explore" if explore or not build else "build"
    return {"mode": mode, "urgent": bool(URGENT.search(message)), "explore_hit": explore, "build_hit": build}


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", s.lower()) if w not in {"the", "and", "for", "with", "that", "this", "from"}}


def _gh_json(args: list[str]) -> list:
    try:
        out = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [{"blind": str(exc)}]
    if out.returncode != 0:
        return [{"blind": out.stderr.strip()[:200]}]
    try:
        return json.loads(out.stdout or "[]")
    except ValueError:
        return [{"blind": "gh returned no JSON"}]


def _worktrees() -> list[str]:
    root = HOME.parent
    names = []
    for repo in root.glob("*/.git"):
        try:
            out = subprocess.run(["git", "-C", str(repo.parent), "worktree", "list", "--porcelain"],
                                 capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        names += [line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("branch ")]
    return names


def dedup(title: str, issues: list | None = None, prs: list | None = None, branches: list | None = None) -> dict:
    """CP3: name the existing card, PR or branch before offering a new draft. Overlap = 2+ shared words
    or a shared 60% of the shorter title."""
    want = _words(title)
    if not want:
        return {"matches": [], "blind": []}
    issues = _gh_json(["issue", "list", "-R", REPO, "--state", "open", "--limit", "200", "--json", "number,title,labels"]) if issues is None else issues
    prs = _gh_json(["pr", "list", "-R", REPO, "--state", "open", "--limit", "100", "--json", "number,title"]) if prs is None else prs
    branches = _worktrees() if branches is None else branches
    blind = [r["blind"] for r in issues + prs if isinstance(r, dict) and "blind" in r]
    matches = []

    def score(text: str) -> bool:
        have = _words(text)
        shared = len(want & have)
        return shared >= 2 or (shared >= 1 and shared / max(1, min(len(want), len(have))) >= 0.6)

    for r in issues:
        if "blind" not in r and score(r.get("title", "")):
            matches.append({"kind": "issue", "id": r["number"], "title": r["title"],
                            "labels": [l.get("name") for l in r.get("labels", [])]})
    for r in prs:
        if "blind" not in r and score(r.get("title", "")):
            matches.append({"kind": "pr", "id": r["number"], "title": r["title"]})
    for b in branches:
        if score(b.replace("refs/heads/", "").replace("-", " ").replace("/", " ")):
            matches.append({"kind": "branch", "id": b.replace("refs/heads/", ""), "title": b})
    return {"matches": matches, "blind": blind}


def feature_text(title: str, message: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
    return (
        f"# features/{slug}.feature (draft from the phone, crew#182)\n"
        f"Feature: {title}\n"
        f"  As the founder, from the phone\n"
        f"  I asked: \"{message.strip()}\"\n\n"
        f"  Scenario: {title} is done\n"
        f"    Given the estate as it is today\n"
        f"    When the change is built and merged\n"
        f"    Then the founder can use it and says so (Definition of Done v2.1)\n"
    )


def draft(title: str, message: str, urgent: bool = False, repo: str = "") -> dict:
    """CP5: show the draft and issue the prompt. The nonce is the only key that opens `create`."""
    STATE.mkdir(parents=True, exist_ok=True)
    nonce = secrets.token_hex(6)
    feature = feature_text(title, message)
    rec = {"nonce": nonce, "title": title, "message": message, "feature": feature, "urgent": urgent,
           "repo": repo, "created": time.time(), "used": False}
    (STATE / f"{nonce}.json").write_text(json.dumps(rec), encoding="utf-8")
    prompt = (f"Draft for \"{title}\"{' (urgent)' if urgent else ''}. Where does it go?")
    return {"nonce": nonce, "feature": feature, "prompt": prompt, "choices": list(CHOICES)}


def create(nonce: str, choice: str, urgent: bool = False, run=None) -> dict:
    """The only path to the board. Refuses without a live nonce; Drop consumes the nonce and writes nothing."""
    choice = choice.strip().lower().replace(" ", "")
    if choice == "todo":
        choice = "todo"
    if choice not in {"todo", "icebox", "drop"}:
        return {"error": f"choice must be one of todo|icebox|drop, got {choice!r}"}
    path = STATE / f"{re.sub(r'[^a-f0-9]', '', nonce)}.json"
    if not nonce or not path.is_file():
        return {"error": "no draft with that nonce: run `draft` and show the founder the prompt first (CP5)"}
    rec = json.loads(path.read_text(encoding="utf-8"))
    if rec.get("used"):
        return {"error": "that draft was already answered; draft again"}
    rec["used"] = True
    rec["choice"] = choice
    path.write_text(json.dumps(rec), encoding="utf-8")
    if choice == "drop":
        return {"dropped": True, "title": rec["title"]}
    labels = [LABEL[choice]] + (["urgent"] if (urgent or rec.get("urgent")) else [])
    body = (f"From the phone, via the Architect (crew#182). Founder chose **{choice}** on the confirmation prompt.\n\n"
            f"> {rec['message']}\n\n" + (f"Repo: {rec['repo']}\n\n" if rec.get("repo") else "") + f"```gherkin\n{rec['feature']}```\n\nDraft nonce: `{nonce}`.")
    args = ["issue", "create", "-R", REPO, "--title", rec["title"], "--body", body]
    for l in labels:
        args += ["--label", l]
    run = run or (lambda a: subprocess.run(["gh", *a], capture_output=True, text=True, timeout=60))
    out = run(args)
    if out.returncode != 0:
        return {"error": out.stderr.strip()[:300], "labels": labels}
    url = out.stdout.strip().splitlines()[-1]
    m = re.search(r"/issues/(\d+)", url)
    return {"issue": int(m.group(1)) if m else None, "url": url, "labels": labels}


def activate(ref: str) -> dict:
    """CP9: an Icebox issue becomes a draft again; it moves only through `create` (the same gate)."""
    issues = _gh_json(["issue", "list", "-R", REPO, "--label", "icebox", "--state", "open", "--limit", "100",
                       "--json", "number,title,body"])
    hit = None
    for r in issues:
        if "blind" in r:
            return {"error": r["blind"]}
        if ref.strip().lstrip("#") == str(r["number"]) or _words(ref) and _words(ref) <= _words(r["title"]):
            hit = r
            break
    if not hit:
        return {"error": f"no open icebox issue matches {ref!r}", "icebox": [(r["number"], r["title"]) for r in issues]}
    d = draft(hit["title"], f"activate icebox #{hit['number']}: {hit['title']}")
    d["icebox_issue"] = hit["number"]
    d["note"] = f"On To Do: label #{hit['number']} agent-go and remove icebox instead of opening a second issue."
    return d


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("classify").add_argument("message")
    sub.add_parser("dedup").add_argument("title")
    d = sub.add_parser("draft"); d.add_argument("title"); d.add_argument("message", nargs="?", default=""); d.add_argument("--urgent", action="store_true"); d.add_argument("--repo", default="", help="owner/name the dispatcher should check out (body line `Repo:`)")
    c = sub.add_parser("create"); c.add_argument("--nonce", required=True); c.add_argument("--choice", required=True); c.add_argument("--urgent", action="store_true")
    sub.add_parser("activate").add_argument("ref")
    a = ap.parse_args(argv)
    if a.cmd == "classify":
        out = classify(a.message)
    elif a.cmd == "dedup":
        out = dedup(a.title)
    elif a.cmd == "draft":
        out = draft(a.title, a.message or a.title, a.urgent, a.repo)
    elif a.cmd == "create":
        out = create(a.nonce, a.choice, a.urgent)
    else:
        out = activate(a.ref)
    print(json.dumps(out, indent=2))
    return 1 if "error" in out else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
