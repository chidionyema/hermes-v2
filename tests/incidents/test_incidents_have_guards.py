#!/usr/bin/env python3
"""Every incident row must name a lesson, a rung and an artifact.

This is the guard on the guard: it stops a post-mortem being closed with a
sentence instead of a mechanism.
"""
import json
import pathlib
import sys

ROWS = pathlib.Path(__file__).resolve().parents[2] / "estate-evals" / "incidents.jsonl"
RUNGS = {0, 1, 2, 3, 4}


def main() -> int:
    bad = []
    rows = [json.loads(line) for line in ROWS.read_text().splitlines() if line.strip()]
    for r in rows:
        rid = r.get("id", "<no id>")
        lesson = r.get("lesson") or {}
        if not lesson.get("statement"):
            bad.append(f"{rid}: no lesson statement")
        if lesson.get("rung") not in RUNGS:
            bad.append(f"{rid}: rung {lesson.get('rung')!r} is not 0-4")
        if not lesson.get("artifact"):
            bad.append(f"{rid}: no artifact - a lesson with no artifact is a note")
    for b in bad:
        print("FAIL:", b)
    if bad:
        return 1
    print(f"PASS {len(rows)} incident rows, each with a lesson, a rung and an artifact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
