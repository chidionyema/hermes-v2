"""Every requirement points at the paragraph of the spec it came from.

A requirement whose link is broken is a requirement nobody can check the
provenance of, and this repo's whole claim is that nothing closes because
someone says so. The links are generated from the row's section field, so the
way they break is a spec section being renamed or removed, which is silent.

Rung 3 in the ladder: a test, not a note.
"""
import json
import os
import re

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(HOME, "docs", "THE-ARCHITECT.md")
LEDGER = os.path.join(HOME, "REQUIREMENTS.jsonl")


def rows():
    with open(LEDGER) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                yield json.loads(line)


def anchors():
    with open(DOC) as f:
        return set(re.findall(r'<a id="([^"]+)"></a>', f.read()))


def test_spec_document_is_in_the_repo():
    # It used to live only in ~/.claude/research, where a clone cannot see it.
    assert os.path.exists(DOC), f"{DOC} is missing"


def test_every_requirement_deep_links_into_the_spec():
    have = anchors()
    broken = []
    for r in rows():
        link = r.get("spec")
        if not link:
            broken.append((r["id"], "no spec link at all"))
            continue
        path, _, frag = link.partition("#")
        if path != "docs/THE-ARCHITECT.md":
            broken.append((r["id"], f"points outside the spec: {path}"))
        elif frag not in have:
            broken.append((r["id"], f"#{frag} is not a heading in the spec"))
    assert not broken, "broken spec links: " + "; ".join(f"{i} {w}" for i, w in broken)


def test_the_link_matches_the_row_s_own_section():
    for r in rows():
        want = "s" + r["section"].lstrip("§").lower()
        assert r["spec"].endswith("#" + want), (
            f"{r['id']} says section {r['section']} but links to {r['spec']}"
        )
