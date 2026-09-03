"""The compute lanes may not know what a Telegram is.

Founder directive, 2026-09-03: the agent lanes accept a standardised
internal envelope and nothing else. A channel's name belongs in exactly
three places — the plugin that reads its credential, the surface binding
that parses its payload, and the ``TaskSource`` value recorded as
provenance. Anywhere else it is coupling, and coupling is what makes
selling to a customer on Teams a platform change instead of a database
row.

This test is the machine that enforces it, so the rule survives the
session that wrote it. It reads the source with ``ast`` rather than
grepping: a docstring that mentions Telegram as an example is prose, and
prose is not coupling. A function, class or module-level constant *named*
after a channel is.

``otto/boot`` is listed as legacy on purpose. It is the single-tenant
Telegram lane the gateway replaces; it is fenced here rather than
deleted, because it is what the cluster is running right now. When the
gateway takes its traffic, the package goes and this exemption goes with
it — and until then, this test stops the pattern spreading.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

#: Channel names, matched as whole words inside an identifier. "Signal"
#: the messenger is deliberately absent: it is also an ordinary English
#: word this codebase uses for other things, and a guard with a false
#: positive gets switched off, which is worse than the gap.
CHANNEL_WORDS = ("telegram", "slack", "whatsapp", "teams", "discord")

OTTO = pathlib.Path(__file__).resolve().parents[2]

#: The three places a channel name is legitimate, plus the legacy lane.
EXEMPT = (
    "ingress/plugins.py",  # the verifier plugins: one channel each, by design
    "surface/bindings/",  # the payload parsers: one channel each, by design
    "boot/",  # legacy single-tenant lane, replaced by otto/ingress
    "tests/",  # tests name channels to prove things about them
    "evals/",  # fixtures replay real transcripts
)


def _compute_modules() -> list[pathlib.Path]:
    modules = []
    for path in sorted(OTTO.rglob("*.py")):
        relative = path.relative_to(OTTO).as_posix()
        if any(relative.startswith(prefix) for prefix in EXEMPT):
            continue
        modules.append(path)
    return modules


def _is_enum(node: ast.ClassDef) -> bool:
    """An enum's members are recorded values, not code that branches, so
    ``TaskSource.telegram`` is allowed and this walk does not descend
    into one."""
    return any("enum" in ast.unparse(base).lower() for base in node.bases)


def _channel_named_definitions(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders: list[str] = []

    def flag(name: str, lineno: int) -> None:
        if any(word in name.lower() for word in CHANNEL_WORDS):
            offenders.append(f"{path.relative_to(OTTO).as_posix()}:{lineno} {name}")

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                flag(child.name, child.lineno)
                if isinstance(child, ast.ClassDef) and _is_enum(child):
                    continue
            elif isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                flag(child.id, child.lineno)
            walk(child)

    walk(tree)
    return offenders


def test_the_scan_actually_looks_at_something() -> None:
    """A guard that inspects an empty list is the silent-green class."""
    modules = _compute_modules()
    assert len(modules) > 20, f"only found {len(modules)} compute modules to check"
    assert any(m.as_posix().endswith("router/render.py") for m in modules)


@pytest.mark.parametrize(
    "module", _compute_modules(), ids=lambda p: p.relative_to(OTTO).as_posix()
)
def test_no_compute_module_names_a_channel_in_its_code(
    module: pathlib.Path,
) -> None:
    offenders = _channel_named_definitions(module)
    assert offenders == [], (
        "a compute lane named a channel in its code: "
        + ", ".join(offenders)
        + ". The lanes read the neutral envelope; the channel belongs in "
        "otto/ingress/plugins.py or otto/surface/bindings/."
    )


def test_the_enum_value_that_records_provenance_is_still_allowed() -> None:
    """A task must be able to say where it came from. That is a recorded
    fact, not a branch — this test exists so a later reader knows the
    difference is deliberate."""
    from otto.spine.envelope import TaskSource

    assert TaskSource.telegram.value == "telegram"
