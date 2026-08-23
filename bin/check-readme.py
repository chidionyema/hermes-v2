#!/usr/bin/env python3
"""Fail if README.md no longer describes what this repo actually ships.

A README goes stale the same way a health check goes blind: nobody notices,
because nothing fails. Two things here are machine-knowable, so neither is
allowed to drift.

  the generated files  templates/**.tmpl is the truth; the README table must
                       list every one of them, exactly once, and nothing else
  the cron jobs        cron/*.jobs.tmpl is the truth; the README table must
                       carry every job name with its real schedule string
  everything tracked   `git ls-files` is the truth; the README table must give
                       every tracked file and every directory holding one a
                       reason to exist, exactly once, and describe nothing that
                       is not there

What a file or a job *does* is prose, and no script can check prose. This
checks the parts that can be checked, which are exactly the parts that rot:
a file added and never documented, a schedule changed in one place only.

  bin/check-readme.py           exit 1 and say what drifted
  bin/check-readme.py --list    print what the README ought to contain
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(HOME, "README.md")
TEMPLATES = os.path.join(HOME, "templates")

FILES_BLOCK = "files"
CRON_BLOCK = "cron"
TRACKED_BLOCK = "tracked"


def block(text, name):
    """The rows between <!-- name --> and <!-- /name -->."""
    m = re.search(
        r"<!--\s*" + re.escape(name) + r"\b.*?-->(.*?)<!--\s*/" + re.escape(name) + r"\s*-->",
        text,
        re.S,
    )
    if not m:
        sys.exit(
            f"check-readme: README.md has no <!-- {name} --> ... <!-- /{name} --> block.\n"
            f"              That block is what makes this checkable. Put it back."
        )
    return m.group(1)


def first_cells(rows):
    """Every `code span` in column 1 of a markdown table, plus column 2."""
    out = []
    for line in rows.split("\n"):
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("- :"):
            continue
        got = re.findall(r"`([^`]+)`", cells[0])
        if got:
            out.append((got[0], cells[1]))
    return out


def rendered_paths():
    """Ask bin/render, so there is one definition of what gets generated."""
    # bin/render has no .py suffix, so it needs an explicit source loader.
    path = os.path.join(HOME, "bin", "render")
    spec = importlib.util.spec_from_loader("render", importlib.machinery.SourceFileLoader("render", path))
    render = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(render)
    except SystemExit as e:
        sys.exit(f"check-readme: bin/render would not load: {e}")
    return {rel for _src, _dest, rel, _seed in render.targets()}


def scheduled_jobs():
    """name -> schedule, from the job templates, which install actually reads."""
    jobs = {}
    src = os.path.join(TEMPLATES, "cron")
    for name in sorted(os.listdir(src)):
        if not name.endswith(".jobs.tmpl"):
            continue
        with open(os.path.join(src, name)) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                job = json.loads(line)
                jobs[job["name"]] = job["schedule"]
    return jobs


def tracked_paths():
    """Every tracked file, plus every directory that holds one.

    `git ls-files` is the definition of what a clone carries, so it is the only
    honest answer to "what is in this repo". Directories are derived rather than
    listed because git does not track them, and a folder nobody can justify is
    the same problem as a file nobody can justify.
    """
    out = subprocess.run(
        ["git", "-C", HOME, "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    files = {p for p in out.split("\n") if p}
    # Evidence images are the exception, and only the images. `pr-evidence.py`
    # names each capture after the moment it was taken, so a re-render lands a
    # new filename and the inventory goes red for a file whose purpose has not
    # changed. That happened twice on PR #1 and both times the fix was to
    # paste the old sentence under a new name, which teaches nobody anything.
    # The directory row still has to exist and still has to say why the folder
    # is here; it is the per-image rows that carry no information.
    dirs = set()
    # Derived from every tracked file, including the evidence images filtered
    # out below. The folders still have to be justified; only the images inside
    # them are exempt, so this loop runs before the filter and not after.
    for f in files:
        d = os.path.dirname(f)
        while d:
            dirs.add(d + "/")
            d = os.path.dirname(d)
    files = {f for f in files
             if not re.fullmatch(r"docs/evidence/pr-\d+/[^/]+", f)}
    return files | dirs


def main():
    want_files = rendered_paths()
    want_jobs = scheduled_jobs()
    want_tracked = tracked_paths()

    if "--list" in sys.argv:
        for rel in sorted(want_files):
            print(f"| `{rel}` | |")
        print()
        for name in sorted(want_jobs):
            print(f"| `{name}` | `{want_jobs[name]}` | |")
        print()
        for rel in sorted(want_tracked):
            print(f"| `{rel}` | |")
        return 0

    with open(README) as f:
        text = f.read()

    problems = []

    documented = first_cells(block(text, FILES_BLOCK))
    listed = [p for p, _ in documented]
    for rel in sorted(want_files - set(listed)):
        problems.append(f"  templates/ generates {rel}, and the README never mentions it")
    for rel in sorted(set(listed) - want_files):
        problems.append(f"  the README describes {rel}, which nothing generates any more")
    for rel in sorted({p for p in listed if listed.count(p) > 1}):
        problems.append(f"  the README lists {rel} more than once")
    for rel, what in documented:
        if not what:
            problems.append(f"  the README lists {rel} with no description")

    jobs = dict(first_cells(block(text, CRON_BLOCK)))
    for name in sorted(set(want_jobs) - set(jobs)):
        problems.append(f"  the schedule has a job named {name}, and the README never mentions it")
    for name in sorted(set(jobs) - set(want_jobs)):
        problems.append(f"  the README describes a job named {name}, which is not on the schedule")
    for name in sorted(set(jobs) & set(want_jobs)):
        said = re.findall(r"`([^`]+)`", jobs[name])
        said = said[0] if said else jobs[name]
        if said != want_jobs[name]:
            problems.append(
                f"  {name} runs at '{want_jobs[name]}', and the README says '{said}'"
            )

    # Every file and every folder has to say why it is here. A repo grows a file
    # nobody can account for one file at a time, and the moment to ask is when it
    # arrives, not a year later when nobody remembers.
    justified = first_cells(block(text, TRACKED_BLOCK))
    named = [p for p, _ in justified]
    for rel in sorted(want_tracked - set(named)):
        problems.append(f"  {rel} is in the repo, and the README never says why")
    for rel in sorted(set(named) - want_tracked):
        problems.append(f"  the README justifies {rel}, which is not in the repo")
    for rel in sorted({p for p in named if named.count(p) > 1}):
        problems.append(f"  the README justifies {rel} more than once")
    for rel, why in justified:
        if not why:
            problems.append(f"  the README lists {rel} with no reason to exist")

    if problems:
        print("README.md no longer describes what this repo ships:")
        print("\n".join(problems))
        print("\nFix README.md. `bin/check-readme.py --list` prints the rows it wants.")
        return 1

    print(
        f"PASS README describes every generated file, every cron job and every "
        f"tracked path ({len(want_files)} generated, {len(want_jobs)} jobs, "
        f"{len(want_tracked)} tracked)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
