"""`otto` CLI — the two commands spec §17 Phase 0 asks for: `otto replay
<task_id>` and `otto eval run --suite core`, plus `otto inventory
--verify-signature` (§15). Stdlib `argparse` only; the estate's own
`fire` (already pinned in hermes-agent) is a fine choice too but this is
a three-command CLI with no nested option groups, and argparse needs no
new dependency at all for that. Not wired into any existing entry_points
table — that would mean editing an existing file, which this task
refuses by construction; this module is invoked directly
(`python -m otto.spine.cli ...`) until a later checkpoint decides how the
whole `otto` binary is packaged.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg

from otto.spine import eval_runner, inventory, replay


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="otto")
    sub = p.add_subparsers(dest="command", required=True)

    replay_p = sub.add_parser(
        "replay", help="reconstruct a task end-to-end from JetStream alone"
    )
    replay_p.add_argument("task_id")

    eval_p = sub.add_parser("eval", help="eval harness")
    eval_sub = eval_p.add_subparsers(dest="eval_command", required=True)
    eval_run_p = eval_sub.add_parser(
        "run", help="run an eval suite and record the baseline"
    )
    eval_run_p.add_argument("--suite", required=True)
    eval_run_p.add_argument(
        "--corpus", required=True, type=Path, help="path to the corpus YAML file"
    )
    eval_run_p.add_argument("--postgres-dsn", default=None)

    inv_p = sub.add_parser(
        "inventory", help="generate, sign and verify the capability inventory"
    )
    inv_p.add_argument("--verify-signature", action="store_true")
    inv_p.add_argument(
        "--previous",
        type=Path,
        default=None,
        help="path to a previous signed inventory JSON",
    )
    inv_p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="write the signed artifact here for the next run",
    )
    inv_p.add_argument(
        "--key-path",
        type=Path,
        default=None,
        help="override the Ed25519 key path (default: env/LAW46 fallback)",
    )

    return p


async def _run(args: argparse.Namespace) -> int:
    if args.command == "replay":
        return await replay.replay_cli(args.task_id)

    if args.command == "eval" and args.eval_command == "run":
        from otto.spine.outbox import default_dsn

        dsn = args.postgres_dsn or default_dsn()
        pool = await asyncpg.create_pool(dsn=dsn)
        try:
            return await eval_runner.eval_run_cli(
                pool, suite=args.suite, corpus_path=args.corpus
            )
        finally:
            await pool.close()

    if args.command == "inventory":
        if not args.verify_signature:
            print(
                "otto inventory: --verify-signature is required (no unsigned path exists)",
                file=sys.stderr,
            )
            return 2
        return inventory.inventory_cli(
            key_path=args.key_path, previous_path=args.previous, output_path=args.output
        )

    raise AssertionError(f"unhandled command {args.command!r}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
