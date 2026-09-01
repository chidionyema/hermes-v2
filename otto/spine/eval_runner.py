"""`otto eval run --suite core` (spec §11, §17 Phase 0: "eval corpus v1 +
runner + baseline recorded").

Honest scope for this checkpoint: spec §11 wants a 40-60 task corpus
"extracted from real Otto and Telegram history" — that extraction is
measurement/analysis work belonging to the eval-harness checkpoint
(crew#768's CP0 lane), not the event spine. What CP1 owns and delivers
here is the *runner and report plumbing* the spec's Phase-0 acceptance
test actually checks mechanically: a suite runs against a corpus file, a
row lands in Postgres `eval_runs`, and the report names the five graded
dimensions per task. It runs against a synthetic fixture corpus
(`otto/tests/cp1/fixtures/eval_corpus_core.yaml`, 40 rows spanning the
six §3 task classes) so this checkpoint's own suite is self-contained;
swapping in the real history-derived corpus is a fixture-file change, not
a runner change, once that extraction exists.

Grading is mechanical, not model-graded, for the same reason: this
runner does not call a hosted model (Phase 4 wires the router). Each
corpus row already carries its own simulated `response` and `claims`, so
"correctness" is a golden-string match and "groundedness" is the
mechanical evidence-refs check the spec explicitly allows ("checked
mechanically where possible", §11) — the part that is not yet mechanical
(model-graded rubric tasks) is out of scope and said so in the report.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import asyncpg
import yaml

SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_runs (
  id          uuid PRIMARY KEY,
  suite       text NOT NULL,
  corpus_size int NOT NULL,
  results     jsonb NOT NULL,
  started_at  timestamptz NOT NULL,
  finished_at timestamptz NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);
"""


@dataclass(frozen=True)
class Claim:
    text: str
    evidence_refs: list[str]


@dataclass(frozen=True)
class EvalTask:
    id: str
    task_class: str
    golden: str
    response: str
    claims: list[Claim]
    tool_path: list[str]
    expected_tool_path: list[str]
    latency_s: float
    cost_usd: float


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    task_class: str
    correctness: float
    groundedness: float
    tool_path_valid: bool
    latency_s: float
    cost_usd: float


def load_corpus(path: Path) -> list[EvalTask]:
    rows = yaml.safe_load(path.read_text())
    out = []
    for r in rows:
        claims = [
            Claim(text=c["text"], evidence_refs=list(c.get("evidence_refs", [])))
            for c in r.get("claims", [])
        ]
        out.append(
            EvalTask(
                id=r["id"],
                task_class=r["class"],
                golden=r["golden"],
                response=r["response"],
                claims=claims,
                tool_path=list(r.get("tool_path", [])),
                expected_tool_path=list(r.get("expected_tool_path", [])),
                latency_s=float(r.get("latency_s", 0.0)),
                cost_usd=float(r.get("cost_usd", 0.0)),
            )
        )
    return out


def grade(task: EvalTask) -> TaskResult:
    correctness = 1.0 if task.response.strip() == task.golden.strip() else 0.0
    grounded = [c for c in task.claims if c.evidence_refs]
    groundedness = (len(grounded) / len(task.claims)) if task.claims else 1.0
    tool_path_valid = task.tool_path == task.expected_tool_path
    return TaskResult(
        task_id=task.id,
        task_class=task.task_class,
        correctness=correctness,
        groundedness=groundedness,
        tool_path_valid=tool_path_valid,
        latency_s=task.latency_s,
        cost_usd=task.cost_usd,
    )


@dataclass
class SuiteReport:
    suite: str
    corpus_size: int
    results: list[TaskResult]
    started_at: str
    finished_at: str

    def mean(self, field_name: str) -> float:
        if not self.results:
            return 0.0
        return sum(getattr(r, field_name) for r in self.results) / len(self.results)

    def as_dict(self) -> dict:
        return {
            "suite": self.suite,
            "corpus_size": self.corpus_size,
            "results": [asdict(r) for r in self.results],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": {
                "mean_correctness": self.mean("correctness"),
                "mean_groundedness": self.mean("groundedness"),
                "tool_path_valid_rate": sum(
                    1 for r in self.results if r.tool_path_valid
                )
                / len(self.results)
                if self.results
                else 0.0,
                "mean_latency_s": self.mean("latency_s"),
                "mean_cost_usd": self.mean("cost_usd"),
            },
        }


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)


async def run_suite(
    pool: asyncpg.Pool, *, suite: str, corpus_path: Path
) -> SuiteReport:
    import datetime

    corpus = load_corpus(corpus_path)
    started = datetime.datetime.now(datetime.timezone.utc)
    results = [grade(t) for t in corpus]
    finished = datetime.datetime.now(datetime.timezone.utc)

    report = SuiteReport(
        suite=suite,
        corpus_size=len(corpus),
        results=results,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
    )

    await ensure_schema(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO eval_runs (id, suite, corpus_size, results, started_at, finished_at) "
            "VALUES ($1, $2, $3, $4::jsonb, $5, $6)",
            uuid.uuid4(),
            suite,
            report.corpus_size,
            json.dumps([asdict(r) for r in results]),
            started,
            finished,
        )
    return report


async def eval_run_cli(pool: asyncpg.Pool, *, suite: str, corpus_path: Path) -> int:
    report = await run_suite(pool, suite=suite, corpus_path=corpus_path)
    print(f"suite: {report.suite}  corpus_size: {report.corpus_size}")
    for r in report.results:
        print(
            f"  {r.task_id} [{r.task_class}] correctness={r.correctness} "
            f"groundedness={r.groundedness:.2f} tool_path_valid={r.tool_path_valid} "
            f"latency_s={r.latency_s} cost_usd={r.cost_usd}"
        )
    s = report.as_dict()["summary"]
    print(
        f"summary: mean_correctness={s['mean_correctness']:.2f} "
        f"mean_groundedness={s['mean_groundedness']:.2f} "
        f"tool_path_valid_rate={s['tool_path_valid_rate']:.2f} "
        f"mean_latency_s={s['mean_latency_s']:.2f} mean_cost_usd={s['mean_cost_usd']:.4f}"
    )
    return 0
