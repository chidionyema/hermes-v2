#!/usr/bin/env python3
"""
claim-check.py — refuse to dispatch a subagent onto a checkpoint someone already claimed.

Root cause this exists for (2026-08-26): a session dispatched a fresh `claude -p` worker
onto crew#284 CP2 fifteen minutes after another session had already posted `CLAIM CP2
<id>` and real receipts (open PR idp#179). The dispatching session never checked comment
history first — it trusted the checkbox in the issue BODY, which does not flip to [x]
until the PR merges, so an in-progress claim looked identical to an unclaimed row.

This is not a "remember to check" fix. It is a command that must be run, and must exit
0, before any `claude -p` dispatch onto a checkpoint-labelled issue (CPn / CLAIM pattern).
Wire it into whatever launches subagents: `claim-check.py <repo> <issue> <checkpoint-id>
|| exit` before the dispatch line, every time, no exceptions for "this one's probably fine".

Exit 0  -> checkpoint has no CLAIM comment and is not ticked [x] in the body. Safe to dispatch.
Exit 1  -> checkpoint is already claimed or already done. Refuses. Prints who/what/when.
Exit 2  -> could not determine (gh failure, malformed issue). Refuses — BLIND is not safe,
           per the estate's own rule that a check which cannot reach its evidence must
           return BLIND, never a pass (crew#164).
"""
import json
import re
import subprocess
import sys


def gh_json(args):
    out = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        return None, out.stderr.strip()
    try:
        return json.loads(out.stdout), None
    except json.JSONDecodeError as e:
        return None, f"malformed JSON from gh: {e}"


def main():
    if len(sys.argv) != 4:
        print("usage: claim-check.py <owner/repo> <issue-number> <checkpoint-id e.g. CP2>", file=sys.stderr)
        return 2

    repo, issue_num, checkpoint = sys.argv[1], sys.argv[2], sys.argv[3]

    data, err = gh_json(["issue", "view", issue_num, "--repo", repo, "--json", "body,comments,state"])
    if data is None:
        print(f"BLIND: could not read {repo}#{issue_num}: {err}", file=sys.stderr)
        return 2

    if data.get("state") == "CLOSED":
        print(f"REFUSE: {repo}#{issue_num} is CLOSED. Nothing to dispatch onto.")
        return 1

    # 1. Is the checkpoint already ticked [x] in the issue body?
    body = data.get("body", "")
    checkbox_pat = re.compile(
        r"- \[( |x)\]\s*\*?\*?" + re.escape(checkpoint) + r"\b", re.IGNORECASE
    )
    m = checkbox_pat.search(body)
    if m and m.group(1).lower() == "x":
        print(f"REFUSE: {checkpoint} is already ticked [x] in {repo}#{issue_num}'s body.")
        return 1

    # 2. Does any comment contain a CLAIM line for this checkpoint?
    claim_pat = re.compile(r"\bCLAIM\s+" + re.escape(checkpoint) + r"\b", re.IGNORECASE)
    for c in data.get("comments", []):
        cbody = c.get("body", "")
        if claim_pat.search(cbody):
            who = c.get("author", {}).get("login", "unknown")
            when = c.get("createdAt", "unknown time")
            print(
                f"REFUSE: {checkpoint} on {repo}#{issue_num} already claimed.\n"
                f"  by: {who}\n  at: {when}\n  comment: {cbody[:200]}"
            )
            return 1

    print(f"OK: {checkpoint} on {repo}#{issue_num} has no CLAIM comment and is not ticked [x]. Safe to dispatch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
