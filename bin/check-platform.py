#!/usr/bin/env python3
"""Refuse an estate.yaml that still names a platform the estate has left.

Incident 2026-08-26: the live estate.yaml carried three Fly URLs, egress hosts
and `platform: kind: fly` two days after the founder's R1 ruling ("we are not
going back to fly"). bin/verify kept probing dead hosts and reported them as
"stopped, as ordered". Nothing read the file against the ruling, so nothing
failed. This does. Exit 0 when clean, 1 with every offending line otherwise.

Usage: bin/check-platform.py [estate.yaml]
"""
import re
import sys

LEFT = {"fly": re.compile(r"fly\.dev|flyctl|kind:\s*fly\b", re.I)}


def offending(text: str) -> list[str]:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for name, rx in LEFT.items():
            if rx.search(line):
                out.append(f"{n}: {line.strip()}  ({name} was left; R1)")
    return out


def main(path: str) -> int:
    bad = offending(open(path, encoding="utf-8").read())
    print("\n".join(bad) if bad else "no dead platform named")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "estate.yaml"))
