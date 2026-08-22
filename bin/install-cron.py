#!/usr/bin/env python3
"""Create the scheduled jobs described by a .jobs file. Idempotent.

A .jobs file is JSON Lines - one job per line, blank lines and #-comments
ignored. Fields:

  name      required, and the identity: a job whose name already exists is
            left exactly as it is, so running this twice changes nothing
  schedule  required, a cron expression
  prompt    what the agent is told to do. Empty for a script job.
  skills    list of skill names to attach
  script    a file under scripts/, run instead of a prompt
  no_agent  true means no model is involved at all: the script is the job

Usage: bin/install-cron.py cron/watch.jobs [--dry-run]
"""
import json
import os
import subprocess
import sys

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERMES = os.path.join(HOME, "bin", "hermes")


def existing_names():
    out = subprocess.run([HERMES, "cron", "list"], capture_output=True, text=True).stdout
    names = set()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Name:"):
            names.add(line.split(":", 1)[1].strip())
    return names


def load(path):
    jobs = []
    with open(path) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                job = json.loads(line)
            except json.JSONDecodeError as e:
                sys.exit(f"install-cron: {path}:{n} is not valid JSON - {e}")
            for required in ("name", "schedule"):
                if not job.get(required):
                    sys.exit(f"install-cron: {path}:{n} has no '{required}'")
            jobs.append(job)
    return jobs


def argv_for(job):
    argv = [HERMES, "cron", "create", job["schedule"]]
    if job.get("prompt"):
        argv.append(job["prompt"])
    argv += ["--name", job["name"], "--deliver", job.get("deliver", "local")]
    for skill in job.get("skills") or []:
        argv += ["--skill", skill]
    if job.get("script"):
        argv += ["--script", job["script"]]
    if job.get("no_agent"):
        argv.append("--no-agent")
    return argv


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    dry = "--dry-run" in sys.argv
    if not os.path.exists(path):
        sys.exit(f"install-cron: {path} does not exist")

    have = existing_names()
    made, kept, failed = [], [], []
    for job in load(path):
        if job["name"] in have:
            kept.append(job["name"])
            continue
        argv = argv_for(job)
        if dry:
            made.append(job["name"])
            print("  would create " + " ".join(repr(a) for a in argv[2:]))
            continue
        r = subprocess.run(argv, capture_output=True, text=True, cwd=HOME)
        if r.returncode == 0:
            made.append(job["name"])
        else:
            failed.append((job["name"], (r.stderr or r.stdout).strip().splitlines()[-1:]))

    for name in made:
        print(f"  created {name}")
    if kept:
        print(f"  kept {len(kept)} already there: {', '.join(kept)}")
    for name, why in failed:
        print(f"  FAILED {name}: {' '.join(why)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
