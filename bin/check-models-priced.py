#!/usr/bin/env python3
"""Refuse any model this estate cannot price.

Measured 2026-08-22: the first agent run used "claude-haiku-4-5-20251001",
which is not in hermes' price table, so the run reported cost_status "unknown".
A model that cannot be priced produces a bill nobody can see coming.

This reads every model named in config.yaml, profiles/*/config.yaml and
cron/jobs.json, and checks each one against the table hermes actually uses.

Exit 0 = every model is priceable. Exit 1 = at least one is not.
"""
import json
import pathlib
import re
import sys

H = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(H / "hermes-agent"))

try:
    from agent.usage_pricing import _OFFICIAL_DOCS_PRICING as TABLE
except Exception as e:  # the table moved or the package is not installed
    print(f"CANNOT CHECK: {e}")
    sys.exit(1)

PRICED = {model for _provider, model in TABLE}


def models_in_yaml(path: pathlib.Path):
    """Every quoted model value, ignoring comments."""
    out = []
    for i, line in enumerate(path.read_text().splitlines(), 1):
        code = line.split("#", 1)[0]
        m = re.search(r'^\s*(?:default|model)\s*:\s*"([^"]+)"', code)
        if m:
            out.append((f"{path.relative_to(H)}:{i}", m.group(1)))
    return out


found = []
for f in [H / "config.yaml", *sorted((H / "profiles").glob("*/config.yaml"))]:
    if f.exists():
        found += models_in_yaml(f)

jobs_file = H / "cron" / "jobs.json"
if jobs_file.exists():
    data = json.loads(jobs_file.read_text())
    jobs = data if isinstance(data, list) else data.get("jobs", data)
    if isinstance(jobs, dict):
        jobs = list(jobs.values())
    for j in jobs:
        if j.get("model"):
            found.append((f"cron/jobs.json:{j['name']}", j["model"]))

bad = []
print(f"{'where':<42}{'model':<30}{'priced'}")
for where, model in found:
    # A provider prefix like "anthropic/..." is an OpenRouter route, priced
    # separately. Only the bare id is looked up here.
    bare = model.split("/")[-1]
    ok = bare in PRICED
    print(f"{where:<42}{model:<30}{'yes' if ok else 'NO'}")
    if not ok:
        bad.append((where, model))

print()
if bad:
    print("FAIL: these models are not in the price table, so their cost cannot be measured:")
    for where, model in bad:
        near = sorted(p for p in PRICED if p.startswith(model.split("-2")[0][:16]))
        print(f"  {where}  {model}" + (f"   did you mean: {', '.join(near[:3])}" if near else ""))
    sys.exit(1)

cheapest = min(
    ((m, TABLE[("anthropic", m)]) for _p, m in TABLE if _p == "anthropic" and m.startswith("claude-")),
    key=lambda r: (r[1].input_cost_per_million, r[1].output_cost_per_million),
)
print(f"PASS every configured model is priceable ({len(found)} checked)")
print(f"Cheapest Claude in the table: {cheapest[0]} at "
      f"${cheapest[1].input_cost_per_million}/${cheapest[1].output_cost_per_million} per MTok")
