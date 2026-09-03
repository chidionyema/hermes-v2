"""Step definitions for ``features/cp1_spine_and_measurement.feature``
(crew#768 CP1). Every scenario runs against a real `nats-server -js`
subprocess and a real ephemeral Postgres cluster (`conftest.py`) — no
fakes, because the partition and slow-consumer scenarios exist precisely
to prove behaviour a fake broker cannot reproduce (real TCP refusal, real
JetStream ack/redelivery timing).

"An engineer runs" / "CI runs" a command is taken literally: these steps
shell out to `python3 -m otto.spine.cli ...` as a subprocess, with the
test's ephemeral NATS/Postgres endpoints passed in as the same env vars
(`OTTO_NATS_URL`, `OTTO_POSTGRES_DSN`, ...) a real deploy would set (LAW
46) — the only difference from a human's shell is that pytest is the one
typing the command.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import nats.errors
import pytest
from pytest_bdd import given, scenarios, then, when

from otto.spine import inventory as inventory_mod
from otto.spine import replay as replay_mod
from otto.spine.bus import Bus
from otto.spine.envelope import TaskClass, TaskEnvelope, TaskSource, Tier
from otto.spine.lifecycle import Lifecycle
from otto.spine.outbox import Outbox, Relay
from otto.spine.subjects import TaskState, dedupe_id, task_subject
from otto.tests.cp1.conftest import NatsServerHandle, run_async

scenarios("../features/cp1_spine_and_measurement.feature")

_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
_CORPUS_PATH = _FIXTURES_DIR / "eval_corpus_core.yaml"

_CLI = [sys.executable, "-m", "otto.spine.cli"]
_REPO_ROOT = (
    Path(__file__).resolve().parents[4]
)  # otto/tests/cp1/step_defs -> repo root


def _run_cli(
    args: list[str], *, env_overrides: dict[str, str]
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_overrides)
    return subprocess.run(  # noqa: S603 - a literal CLI list, no shell, no caller input
        [*_CLI, *args],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _new_envelope(*, provenance: str) -> TaskEnvelope:
    return TaskEnvelope.new(
        tenant_id="tenant-under-test",
        source=TaskSource.telegram,
        task_class=TaskClass.ops_read,
        input="run the CP1 spine BDD scenario",
        authority_ceiling=Tier.T1,
        provenance=provenance,
    )


# ---------------------------------------------------------------- Background


@given("the staging cluster only, zero production credentials in scope")
def staging_only(nats_handle: NatsServerHandle, postgres_handle) -> None:
    # This suite's "staging cluster" is the real, ephemeral nats-server and
    # Postgres cluster conftest.py starts per session — never a shared or
    # production endpoint (OTTO_NATS_URL / OTTO_POSTGRES_DSN point at them
    # exclusively for the lifetime of this process, LAW 46). Asserting the
    # handles exist is asserting that isolation held.
    assert nats_handle.url.startswith("nats://127.0.0.1:")
    assert postgres_handle.dsn.startswith("postgresql://")


@given(
    "the JetStream streams OTTO_TASKS, OTTO_AUDIT, OTTO_VERDICTS, OTTO_METRICS exist"
)
def streams_exist(bus: Bus) -> None:
    run_async(bus.ensure_streams())
    for name in ("OTTO_TASKS", "OTTO_AUDIT", "OTTO_VERDICTS", "OTTO_METRICS"):
        info = run_async(bus.js.stream_info(name))
        assert info.config.name == name


# ------------------------------------------------------------ Scenario 1 & 4


@given("a task has run to completion on the new Otto build")
def task_ran_to_completion(bus: Bus, ctx: dict) -> None:
    envelope = _new_envelope(provenance="cp1-bdd:scenario-completed-task")
    lifecycle = Lifecycle(bus=bus, task_id=envelope.task_id)
    tool_calls = [
        ("search", {"query": "otto cp1"}, {"hits": 3}),
        ("fs_read", {"path": "README.md"}, {"bytes": 128}),
    ]
    run_async(
        lifecycle.run_to_completion(
            envelope,
            tool_calls=tool_calls,
            verdict_result="pass",
            verdict_evidence={"note": "bdd fixture"},
        )
    )
    ctx["task_id"] = envelope.task_id
    ctx["envelope"] = envelope
    ctx["tool_calls"] = tool_calls
    ctx["expect_tool_calls"] = True


@given("a task that reached completed using only model judgment, no tool calls")
def task_no_tool_calls(bus: Bus, ctx: dict) -> None:
    envelope = _new_envelope(provenance="cp1-bdd:scenario-zero-tool-calls")
    lifecycle = Lifecycle(bus=bus, task_id=envelope.task_id)
    run_async(
        lifecycle.run_to_completion(
            envelope,
            tool_calls=(),
            verdict_result="pass",
            verdict_evidence={"note": "model judgment only"},
        )
    )
    ctx["task_id"] = envelope.task_id
    ctx["envelope"] = envelope
    ctx["tool_calls"] = []
    ctx["expect_tool_calls"] = False


@when('an engineer runs "otto replay <task_id>"')
def run_otto_replay(nats_handle: NatsServerHandle, ctx: dict) -> None:
    proc = _run_cli(
        ["replay", ctx["task_id"]], env_overrides={"OTTO_NATS_URL": nats_handle.url}
    )
    ctx["exit_code"] = proc.returncode
    ctx["stdout"] = proc.stdout
    ctx["stderr"] = proc.stderr


@then("the command exits 0")
def exit_code_is_zero(ctx: dict) -> None:
    assert ctx["exit_code"] == 0, (
        f"stdout={ctx.get('stdout')!r} stderr={ctx.get('stderr')!r}"
    )


@then(
    "the reconstructed task envelope, tool calls and verdict match the original with zero diff"
)
def replay_matches_original(bus: Bus, ctx: dict) -> None:
    result = run_async(replay_mod.replay(bus, ctx["task_id"]))
    original: TaskEnvelope = ctx["envelope"]

    # Zero diff on the envelope: the replayed payload, re-encoded the same
    # canonical way, is byte-identical to the envelope that was submitted.
    assert (
        result.envelope_hash
        == __import__("hashlib").sha256(original.canonical_json()).hexdigest()
    )
    assert json.loads(original.canonical_json()) == result.envelope

    assert len(result.tool_events) == len(ctx["tool_calls"]) * 2  # req + res per call
    tools_seen = [e.payload["tool"] for e in result.tool_events]
    for tool, _, _ in ctx["tool_calls"]:
        assert tools_seen.count(tool) == 2

    assert len(result.verdict_events) == 1
    assert result.verdict_events[0].payload["result"] == "pass"
    assert result.missing_seqs == []


@then("no data outside JetStream was read to produce the replay")
def replay_reads_only_jetstream() -> None:
    # Structural proof, not a runtime mock check: the replay module never
    # imports a database or filesystem-cache client at all, so there is no
    # code path in `replay()` capable of reading anything but the bus.
    src = (Path(replay_mod.__file__)).read_text()
    for forbidden in ("asyncpg", "sqlite3", "open(", "psycopg"):
        assert forbidden not in src, f"replay.py must never touch {forbidden!r}"


@then("the replay shows an empty tool-call list, not an error")
def replay_shows_empty_tool_calls(ctx: dict) -> None:
    assert ctx["exit_code"] == 0
    assert "tool_calls: 0 " in ctx["stdout"]
    assert "missing_seqs: none" in ctx["stdout"]


# --------------------------------------------------------------- Scenario 2


@given(
    "the 40 to 60 task synthetic eval corpus standing in for real Otto and "
    "Telegram history (extraction is CP0's harness job)"
)
def eval_corpus_present(ctx: dict) -> None:
    assert _CORPUS_PATH.is_file(), _CORPUS_PATH
    import yaml

    rows = yaml.safe_load(_CORPUS_PATH.read_text())
    assert 40 <= len(rows) <= 60, f"corpus has {len(rows)} rows, spec wants 40-60"
    ctx["corpus_size"] = len(rows)


@when('an engineer runs "otto eval run --suite core"')
def run_otto_eval(postgres_handle, ctx: dict) -> None:
    proc = _run_cli(
        [
            "eval",
            "run",
            "--suite",
            "core",
            "--corpus",
            str(_CORPUS_PATH),
            "--postgres-dsn",
            postgres_handle.dsn,
        ],
        env_overrides={},
    )
    ctx["exit_code"] = proc.returncode
    ctx["stdout"] = proc.stdout
    ctx["stderr"] = proc.stderr


@then("a row is written to the eval_runs table in Postgres for suite core")
def eval_row_written(pg_pool) -> None:
    row = run_async(
        pg_pool.fetchrow(
            "SELECT suite, corpus_size FROM eval_runs WHERE suite = $1 ORDER BY created_at DESC LIMIT 1",
            "core",
        )
    )
    assert row is not None, "no eval_runs row for suite=core"
    assert row["suite"] == "core"
    assert row["corpus_size"] >= 40


@then(
    "the report names correctness, groundedness, tool-path validity, latency and cost per task"
)
def eval_report_names_dimensions(ctx: dict) -> None:
    out = ctx["stdout"]
    for token in (
        "correctness=",
        "groundedness=",
        "tool_path_valid=",
        "latency_s=",
        "cost_usd=",
    ):
        assert token in out, f"{token!r} missing from eval report:\n{out}"


# --------------------------------------------------------------- Scenario 3


@given(
    "the current tool, credential-handle, ServiceAccount, egress-domain and lane-budget config"
)
def current_config_and_previous_deploy_snapshot(tmp_path: Path, ctx: dict) -> None:
    # CP1's inventory scope is the bus/subject/module config it owns (see
    # otto/spine/inventory.py's module docstring for the honest boundary
    # with CP2/Phase 1's tool-credential-ServiceAccount inventory). To make
    # "a diff against the previous deploy's inventory" a real, checkable
    # artifact rather than an assertion about nothing, this Given seeds a
    # stand-in "previous deploy" snapshot: today's inventory with one
    # component's version deliberately rolled back, exactly the shape a
    # real prior CI artifact would have after a dependency bump.
    current = inventory_mod.generate_inventory(generated_at=datetime.now(timezone.utc))
    components = current["components"]
    assert components, "inventory generated zero components"
    drifted = dict(components[0])
    drifted["version"] = drifted["version"] + "-previous-deploy"
    previous = {**current, "components": [drifted, *components[1:]]}

    key_path = tmp_path / "inventory_ed25519.pem"
    # A real previous-deploy artifact was itself the output of a signed
    # `otto inventory` run — sign this stand-in the same way, with the
    # same key the CLI invocation below will load, or the CLI's own
    # fail-closed check on --previous (a tampered/unsigned artifact is
    # refused, never silently diffed) correctly rejects it.
    key = inventory_mod.load_or_create_keypair(key_path)
    signed_previous = inventory_mod.sign_inventory(previous, key)

    previous_path = tmp_path / "previous_inventory.json"
    previous_path.write_text(json.dumps(signed_previous))
    ctx["previous_inventory_path"] = previous_path
    ctx["output_inventory_path"] = tmp_path / "current_inventory.json"
    ctx["inventory_key_path"] = key_path


@when('CI runs "otto inventory --verify-signature"')
def run_otto_inventory(ctx: dict) -> None:
    proc = _run_cli(
        [
            "inventory",
            "--verify-signature",
            "--previous",
            str(ctx["previous_inventory_path"]),
            "--output",
            str(ctx["output_inventory_path"]),
            "--key-path",
            str(ctx["inventory_key_path"]),
        ],
        env_overrides={},
    )
    ctx["exit_code"] = proc.returncode
    ctx["stdout"] = proc.stdout
    ctx["stderr"] = proc.stderr


@then(
    "the emitted inventory is a signed artifact distinct from any hand-maintained list"
)
def inventory_is_signed_artifact(ctx: dict) -> None:
    assert "signature_valid: True" in ctx["stdout"], ctx["stdout"]
    signed = json.loads(ctx["output_inventory_path"].read_text())
    assert set(signed.keys()) == {"inventory", "key_id", "signature"}
    assert signed["inventory"]["components"], (
        "components must be generated, not hand-typed to empty"
    )

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key_bytes = ctx["inventory_key_path"].read_bytes()
    private_key = serialization.load_pem_private_key(key_bytes, password=None)
    assert isinstance(private_key, Ed25519PrivateKey)
    assert inventory_mod.verify_signed_inventory(signed, private_key.public_key())


@then("a diff against the previous deploy's inventory is attached to the CI run")
def inventory_diff_attached(ctx: dict) -> None:
    # "attached to the CI run" is satisfied by the printed summary line
    # plus the persisted --output artifact a real CI job would upload. The
    # Given step drifted exactly one component's version and touched no
    # other row, so the diff must show exactly one changed row and zero
    # added/removed — proving this is computed from real content, not a
    # stub that always prints "no changes".
    diff_line = next(
        row for row in ctx["stdout"].splitlines() if row.startswith("diff: ")
    )
    assert diff_line == "diff: +0 -0 ~1", (
        f"expected exactly one changed row, got {diff_line!r}"
    )


# --------------------------------------------------------------- Scenario 5


@given("a task is submitted through the Postgres transactional outbox")
def task_submitted_via_outbox(pg_pool, ctx: dict) -> None:
    envelope = _new_envelope(provenance="cp1-bdd:scenario-partition")
    outbox = Outbox(pg_pool)
    run_async(outbox.ensure_schema())

    async def _enqueue() -> None:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                await outbox.enqueue(
                    conn,
                    task_id=envelope.task_id,
                    seq=1,
                    subject=task_subject(TaskState.submitted),
                    payload=envelope.canonical_json(),
                )

    run_async(_enqueue())
    ctx["task_id"] = envelope.task_id
    ctx["envelope"] = envelope


@given("NATS JetStream is partitioned before the relay publishes the submission event")
def partition_before_relay(
    nats_handle: NatsServerHandle, bus: Bus, pg_pool, ctx: dict
) -> None:
    nats_handle.stop()

    relay = Relay(pg_pool, bus)
    # nats.errors.Error is the base of every failure mode the client raises
    # for a dead server (NoServersError, TimeoutError, ConnectionClosedError,
    # ...); named here instead of bare Exception so a genuine bug in the
    # relay's own code (a TypeError, an AttributeError) still fails loud.
    with pytest.raises(nats.errors.Error):
        run_async(relay.once())

    unpublished = run_async(_count_unpublished(pg_pool))
    assert unpublished >= 1, "the row must still be unpublished while NATS is down"


async def _count_unpublished(pg_pool) -> int:
    async with pg_pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM otto_outbox WHERE published_at IS NULL"
        )


@when("the partition heals")
def partition_heals(nats_handle: NatsServerHandle, pg_pool, ctx: dict) -> None:
    nats_handle.start()

    async def _heal():
        healed_bus = await Bus(servers=[nats_handle.url]).connect()
        await healed_bus.ensure_streams()
        relay = Relay(pg_pool, healed_bus)
        results = await relay.once()
        return healed_bus, results

    healed_bus, results = run_async(_heal())
    ctx["healed_bus"] = healed_bus
    ctx["relay_results"] = results


@then("the outbox relay publishes the pending event with its Nats-Msg-Id intact")
def relay_republished_with_msg_id(pg_pool, ctx: dict) -> None:
    results = ctx["relay_results"]
    assert len(results) == 1
    result = results[0]
    assert result.task_id == ctx["task_id"]
    assert result.seq == 1

    unpublished = run_async(_count_unpublished(pg_pool))
    assert unpublished == 0

    bus: Bus = ctx["healed_bus"]
    msgs = run_async(bus.read_all(stream="OTTO_TASKS", filter_subject="otto.task.v1.>"))
    matching = [
        m
        for m in msgs
        if (m.headers or {}).get("Nats-Msg-Id") == dedupe_id(ctx["task_id"], 1)
    ]
    assert len(matching) == 1, (
        "the published message must carry the exact Nats-Msg-Id the outbox assigned"
    )


@then('"otto replay <task_id>" shows no missing sequence number for that task')
def replay_shows_no_missing_seq_partition(ctx: dict) -> None:
    bus: Bus = ctx["healed_bus"]
    result = run_async(replay_mod.replay(bus, ctx["task_id"]))
    assert result.missing_seqs == []


# --------------------------------------------------------------- Scenario 6


@given("a consumer of OTTO_AUDIT is throttled to a fraction of normal throughput")
def throttled_consumer_ready(bus: Bus, ctx: dict) -> None:
    # "throttled" is modelled honestly: a real pull consumer, fetching in
    # small batches with a deliberate sleep between fetches, running
    # concurrently with the publisher below — not a fake queue with an
    # artificial delay bolted onto a mock.
    #
    # The durable consumer is created here, in the Given step, with
    # DeliverPolicy.NEW — it only ever sees events published after this
    # point. OTTO_AUDIT is a shared stream across every scenario in this
    # suite (spec §4: one stream per resource, not per task), so a fresh
    # durable that defaulted to "deliver everything since the stream
    # began" would also redeliver other scenarios' tool events and this
    # scenario's own 500-event count would never match; NEW plus a
    # subject filter on this stream is what keeps the count exact
    # without the subject taxonomy needing a task-scoped subject it does
    # not have (spec §4's four subjects are shared, not per-task).
    envelope = _new_envelope(provenance="cp1-bdd:scenario-slow-consumer")
    lifecycle = Lifecycle(bus=bus, task_id=envelope.task_id)
    run_async(lifecycle.submit(envelope))

    durable = f"cp1-throttle-{envelope.task_id}"
    psub = run_async(
        bus.durable_pull(
            stream="OTTO_AUDIT",
            durable=durable,
            filter_subject="otto.tool.v1.>",
            deliver_all=False,
        )
    )

    ctx["task_id"] = envelope.task_id
    ctx["lifecycle"] = lifecycle
    ctx["psub"] = psub
    ctx["throttle_batch"] = 8
    ctx["throttle_sleep_s"] = 0.02


@when("500 tool req/res events are published during the throttle window")
def publish_500_and_consume_throttled(ctx: dict) -> None:
    lifecycle: Lifecycle = ctx["lifecycle"]
    psub = ctx["psub"]
    n_calls = 250  # 250 req + 250 res = 500 events

    async def _publish() -> None:
        for i in range(n_calls):
            await lifecycle.tool_call(
                f"tool_{i % 5}", args={"i": i}, result={"ok": True}
            )

    async def _consume_throttled() -> int:
        acked = 0
        deadline = asyncio.get_event_loop().time() + 60
        while acked < n_calls * 2 and asyncio.get_event_loop().time() < deadline:
            try:
                batch = await psub.fetch(ctx["throttle_batch"], timeout=2)
            except TimeoutError:
                continue
            for m in batch:
                await m.ack()
                acked += 1
            await asyncio.sleep(ctx["throttle_sleep_s"])
        return acked

    async def _run_both():
        return await asyncio.gather(_publish(), _consume_throttled())

    _, acked_count = run_async(_run_both())
    ctx["expected_events"] = n_calls * 2
    ctx["acked_count"] = acked_count


@then("every event is eventually delivered and acknowledged")
def all_events_acked(ctx: dict) -> None:
    assert ctx["acked_count"] == ctx["expected_events"], (
        f"acked {ctx['acked_count']} of {ctx['expected_events']} — the throttled consumer must still "
        "drain everything, only slower"
    )


@then('"otto replay <task_id>" for a task spanning the throttle window shows zero gaps')
def replay_shows_zero_gaps_after_throttle(bus: Bus, ctx: dict) -> None:
    result = run_async(replay_mod.replay(bus, ctx["task_id"]))
    assert result.missing_seqs == []
    assert len(result.tool_events) == ctx["expected_events"]
