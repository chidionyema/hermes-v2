#!/usr/bin/env python3
"""The oldest `agent-go` issue on the board becomes a worktree, a branch and a
running session. crew#182 CP7 (dispatch), CP8 (no conflicts), CP13 (runtime by config).

The board is GitHub issues; the labels are the columns. `agent-go` is To Do.
`in-progress` means claimed. `icebox` is never read. No model runs here: this
is a cron script job, so a tick costs nothing and an asleep laptop simply
misses ticks; the issue keeps its label and is taken on the first tick after
waking. Nothing is claimed until the session is actually running.

  scripts/dispatch-agent-go.py            one tick, reads estate.yaml `dispatch:`
  scripts/dispatch-agent-go.py --drill D  same logic against D/issues.json and
                                          D/checkout, no GitHub, runtime from
                                          --runtime-cmd (a shell string). bin/verify and the
                                          incident test run this.

Config, in estate.yaml:
  dispatch:
    board: owner/repo          the issues that are the board
    label: agent-go            the column this reads
    claimed: in-progress       the label it adds once a session runs
    never: [icebox]            columns it must not read
    checkouts: ..              where owner/name checkouts live, relative to this repo
    runtime: claude            which entry of runtimes: to start
    runtimes:                  argv templates; {prompt} is the task text
      claude: [claude, -p, "{prompt}", --permission-mode, acceptEdits]

An issue body may carry a line `Repo: owner/name` to work in a checkout other
than the board's own.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS = {
    "label": "agent-go", "claimed": "in-progress", "never": ["icebox"],
    "checkouts": "..", "runtime": "claude",
    "runtimes": {
        "claude": ["claude", "-p", "{prompt}", "--permission-mode", "acceptEdits",
                   "--allowedTools", "Bash(git *) Bash(gh *)"],
        "gemini": ["gemini", "-p", "{prompt}", "--yolo"],
        "opencode": ["opencode", "run", "{prompt}"],
        "codex": ["codex", "exec", "--full-auto", "{prompt}"],
    },
}


def load_config():
    import yaml
    with open(os.path.join(HOME, "estate.yaml")) as f:
        estate = yaml.safe_load(f) or {}
    cfg = dict(DEFAULTS)
    cfg.update(estate.get("dispatch") or {})
    if "board" not in cfg:
        cfg["board"] = estate["github"]["owner"] + "/" + estate["github"]["repo"]
    return cfg


def sh(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def gh_json(*args):
    r = sh(["gh", *args])
    if r.returncode:
        sys.exit(f"dispatch: gh {' '.join(args[:3])} failed: {r.stderr.strip()}")
    return json.loads(r.stdout or "[]")


def pick(issues, cfg):
    """The oldest open issue in the column, not claimed, not in a never column."""
    forbidden = set(cfg["never"]) | {cfg["claimed"]}
    ready = []
    for i in issues:
        labels = {l["name"] if isinstance(l, dict) else l for l in i.get("labels", [])}
        if cfg["label"] in labels and not (labels & forbidden):
            ready.append(i)
    return min(ready, key=lambda i: i["number"]) if ready else None


def target_repo(issue, cfg):
    m = re.search(r"^Repo:\s*([\w.-]+/[\w.-]+)\s*$", issue.get("body") or "", re.M)
    return m.group(1) if m else cfg["board"]


def prompt_for(issue, board, branch):
    return (f"Issue #{issue['number']} on {board}: {issue['title']}\n\n{issue.get('body') or ''}\n\n"
            f"You are in an isolated git worktree on branch {branch}. Do the work here, commit, "
            f"and open a pull request naming {board}#{issue['number']}. Never merge, never deploy.")


def start(issue, checkout, runtime_argv, cfg, log_dir):
    n = issue["number"]
    branch = f"agent-go/{n}"
    wt = os.path.join(checkout, ".worktrees", f"agent-go-{n}")
    if not os.path.isdir(wt):
        sh(["git", "-C", checkout, "fetch", "-q", "origin"])
        base = "origin/HEAD" if sh(["git", "-C", checkout, "rev-parse", "-q", "--verify", "origin/HEAD"]).returncode == 0 else "HEAD"
        r = sh(["git", "-C", checkout, "worktree", "add", "-q", "-b", branch, wt, base])
        if r.returncode:
            return None, f"FAIL #{n}: git worktree add: {r.stderr.strip()}"
    prompt = prompt_for(issue, cfg["board"], branch)
    argv = [a.replace("{prompt}", prompt) for a in runtime_argv]
    exe = shutil.which(argv[0])
    if not exe:
        return None, f"FAIL #{n}: runtime '{argv[0]}' is not on PATH; issue left unclaimed"
    os.makedirs(log_dir, exist_ok=True)
    log = os.path.join(log_dir, f"agent-go-{n}.log")
    env = dict(os.environ, HERMES_DISPATCH_ISSUE=str(n), HERMES_DISPATCH_BOARD=cfg["board"])
    p = subprocess.Popen([exe, *argv[1:]], cwd=wt, stdin=subprocess.DEVNULL,
                         stdout=open(log, "ab"), stderr=subprocess.STDOUT,
                         start_new_session=True, env=env)
    return p, f"CLAIMED #{n} -> {wt} ({branch}) runtime={cfg['runtime']} pid={p.pid} log={log}"


def live(cfg):
    issues = gh_json("issue", "list", "-R", cfg["board"], "--label", cfg["label"], "--state", "open",
                     "-L", "50", "--json", "number,title,body,labels")
    issue = pick(issues, cfg)
    if not issue:
        return 0
    repo = target_repo(issue, cfg)
    checkout = os.path.normpath(os.path.join(HOME, cfg["checkouts"], repo.split("/")[1]))
    if not os.path.isdir(os.path.join(checkout, ".git")):
        print(f"WAIT #{issue['number']}: no checkout of {repo} at {checkout}; issue left unclaimed")
        return 1
    p, line = start(issue, checkout, cfg["runtimes"][cfg["runtime"]], cfg, os.path.join(HOME, "cron", "output", "dispatch"))
    print(line)
    if p is None:
        return 1
    n = str(issue["number"])
    sh(["gh", "issue", "edit", n, "-R", cfg["board"], "--add-label", cfg["claimed"]])
    sh(["gh", "issue", "comment", n, "-R", cfg["board"], "-b",
        f"Dispatched by hermes-v2 work-agent-go (crew#182 CP7): {line}"])
    return 0


def drill(d, runtime_cmd):
    cfg = dict(DEFAULTS, board="drill/board", runtime="drill", runtimes={"drill": runtime_cmd})
    with open(os.path.join(d, "issues.json")) as f:
        issues = json.load(f)
    claims_path = os.path.join(d, "claims.json")
    claimed = json.load(open(claims_path)) if os.path.exists(claims_path) else []
    for i in issues:
        if i["number"] in claimed:
            i.setdefault("labels", []).append(cfg["claimed"])
    issue = pick(issues, cfg)
    if not issue:
        return 0
    p, line = start(issue, os.path.join(d, "checkout"), cfg["runtimes"]["drill"], cfg, os.path.join(d, "log"))
    print(line)
    if p is None:
        return 1
    p.wait()
    json.dump(claimed + [issue["number"]], open(claims_path, "w"))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drill", metavar="DIR")
    ap.add_argument("--runtime-cmd", default="true", help="drill only: a shell string, run in the worktree")
    a = ap.parse_args()
    return drill(a.drill, shlex.split(a.runtime_cmd)) if a.drill else live(load_config())


if __name__ == "__main__":
    sys.exit(main())
