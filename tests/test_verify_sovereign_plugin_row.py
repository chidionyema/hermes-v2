"""crew#284 CP1: `bin/verify` has a row that proves the sovereign plugin registers its commands.

Rung 4 (incident). On 2026-08-26 the CP1 row asked for "a hermes-v2 bin/verify row" and there was
none; the only proof was a session comment saying it had loaded the plugin by hand. Proved both
ways in one run: the real plugin passes, and a plugin that registers nothing fails with the names.
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "bin", "verify-sovereign-plugin")


def _run(*args):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, timeout=60)


def test_real_plugin_registers_every_spec_command():
    p = _run()
    assert p.returncode == 0, p.stdout + p.stderr
    assert p.stdout.startswith("7 commands, hook pre_gateway_dispatch"), p.stdout


def test_a_plugin_registering_nothing_fails_and_names_the_gap(tmp_path):
    (tmp_path / "__init__.py").write_text("def register(ctx):\n    ctx.register_command('sb-list', None)\n")
    p = _run(str(tmp_path))
    assert p.returncode == 1
    assert "missing: sb-show" in p.stdout and "pre_gateway_dispatch" in p.stdout, p.stdout


def test_verify_reads_the_row():
    text = open(os.path.join(ROOT, "bin", "verify")).read()
    assert 'bin/verify-sovereign-plugin' in text and 'row "the sovereign plugin registers its commands"' in text
