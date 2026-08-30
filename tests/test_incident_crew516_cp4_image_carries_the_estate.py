"""crew#516 CP4 (2026-08-27): the OKE image was upstream hermes-agent alone.

Incident: `Dockerfile` cloned the pinned upstream and set HERMES_HOME=/app, so a pod built from it
had no config.yaml, no SOUL.md, no skills/, no cron lanes and nowhere writable to keep a session:
The Architect could not move off the Mac because the image was not The Architect. Rung 4, both
ways: the tree passes; a Dockerfile without the estate COPY, or an entrypoint that overwrites
auth.json, fails.
"""
import os
import re
import subprocess

import pytest
import yaml

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKERFILE = os.path.join(HOME, "Dockerfile")
ENTRYPOINT = os.path.join(HOME, "deploy", "k8s", "entrypoint.sh")
WORKFLOW = os.path.join(HOME, ".github", "workflows", "build-agent-image.yml")
DOCKERIGNORE = os.path.join(HOME, ".dockerignore")
CONFIG = os.path.join(HOME, "config.yaml")


def image_carries_the_estate(dockerfile_text):
    return bool(re.search(r"^COPY\b.*\s\.\s+/app/estate\s*$", dockerfile_text, re.M)) and \
        'ENTRYPOINT ["/app/estate/deploy/k8s/entrypoint.sh"]' in dockerfile_text


def interpreter_is_readable_by_the_pod_user(dockerfile_text):
    """The venv python must not resolve under /root: 10001 cannot traverse it (image main-13-a367cbd)."""
    m = re.search(r'^ENV UV_PYTHON_INSTALL_DIR=(\S+)\s*$', dockerfile_text, re.M)
    if not m or m.group(1).startswith("/root") or m.group(1).startswith("$HOME"):
        return False
    sync = dockerfile_text.find("RUN uv sync")
    return 0 <= m.start() < sync and "chmod -R a+rX " + m.group(1).rsplit("/", 1)[0] in dockerfile_text


def test_the_venv_interpreter_is_installed_where_uid_10001_can_read_it():
    assert interpreter_is_readable_by_the_pod_user(open(DOCKERFILE).read())


def test_a_dockerfile_that_leaves_the_interpreter_under_root_is_refused():
    text = open(DOCKERFILE).read()
    assert not interpreter_is_readable_by_the_pod_user(re.sub(r"^ENV UV_PYTHON_INSTALL_DIR=.*\n", "", text, flags=re.M))
    assert not interpreter_is_readable_by_the_pod_user(text.replace("/opt/uv/python", "/root/.local/share/uv/python"))
    # set after the sync is too late: the venv already points under /root
    moved = re.sub(r"^ENV UV_PYTHON_INSTALL_DIR=.*\n", "", text, flags=re.M).replace(
        'ENV PATH="/app/hermes-agent/.venv/bin:$PATH"', 'ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python\nENV PATH="/app/hermes-agent/.venv/bin:$PATH"')
    assert not interpreter_is_readable_by_the_pod_user(moved)


def test_image_copies_this_repo_and_boots_through_the_entrypoint():
    assert image_carries_the_estate(open(DOCKERFILE).read())


def test_a_dockerfile_without_the_estate_is_refused():
    stripped = re.sub(r"^COPY --chown=\S+ \. /app/estate\n", "", open(DOCKERFILE).read(), flags=re.M)
    assert not image_carries_the_estate(stripped)


def test_entrypoint_keeps_the_state_and_installs_both_lanes():
    text = open(ENTRYPOINT).read()
    assert os.access(ENTRYPOINT, os.X_OK)
    assert "set -euo pipefail" in text
    # build over the volume, state untouched: auth.json only when absent
    assert '[ ! -s "$HERMES_HOME/auth.json" ]' in text
    assert "install-cron.py cron/watch.jobs --feature watch" in text
    assert "install-cron.py cron/work.jobs --feature work" in text
    # crew#524 CP2: the third lane is installed the same way and gated on evolution: on in the estate
    assert "install-cron.py cron/evolution.jobs --feature evolution" in text
    assert re.search(r'^exec .*hermes_cli\.main gateway run', text, re.M)


def _seed_block():
    text = open(ENTRYPOINT).read()
    m = re.search(r'if \[ ! -s "\$HERMES_HOME/auth\.json" \].*?\nfi\n', text, re.S)
    assert m, "the auth.json seed block moved"
    return m.group(0)


def _run_seed(home, value):
    return subprocess.run(["bash", "-euo", "pipefail", "-c", _seed_block()],
                          env={"PATH": os.environ["PATH"], "HERMES_HOME": str(home), "HERMES_AUTH_JSON": value},
                          capture_output=True, text=True)


def test_entrypoint_never_overwrites_a_live_auth_json(tmp_path):
    """The seed block alone: a volume that already holds auth.json keeps it byte for byte."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "auth.json").write_text('{"live": true}')
    assert _run_seed(home, '{"stale": true}').returncode == 0
    assert (home / "auth.json").read_text() == '{"live": true}'


def test_entrypoint_seeds_an_empty_volume_owner_only(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _run_seed(empty, '{"seed": true}').returncode == 0
    assert (empty / "auth.json").read_text() == '{"seed": true}'
    assert oct(os.stat(empty / "auth.json").st_mode & 0o777) == "0o600"


def test_entrypoint_exports_file_mounted_secrets(tmp_path):
    """Kyverno refuses envFrom on the cluster; the Secret is a directory of files, exported here."""
    d = tmp_path / "env"
    d.mkdir()
    (d / "TELEGRAM_BOT_TOKEN").write_text("t0k")
    (d / "..data").write_text("x")  # a projected-volume symlink target; not an env name
    text = open(ENTRYPOINT).read()
    m = re.search(r'if \[ -n "\$\{HERMES_ENV_DIR:-\}" \].*?\nfi\n', text, re.S)
    assert m, "the env-dir export block moved"
    r = subprocess.run(["bash", "-euo", "pipefail", "-c", m.group(0) + 'printf "%s" "$TELEGRAM_BOT_TOKEN"; env | grep -c "^\\.\\.data=" || true'],
                       env={"PATH": os.environ["PATH"], "HERMES_ENV_DIR": str(d)}, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == "t0k0\n", r


# A provider name -> the extra that ships its SDK. `None` means the call leaves through an
# OpenAI-compatible aggregator (the estate router at llm.mumchimp.com, OpenRouter) and needs no
# vendor SDK at all. Every name here is an extra that exists in the fork's pyproject
# [project.optional-dependencies]; a selection this table does not know fails the test rather than
# passing quietly, because a silent miss is how `anthropic` was absent for 15 hours.
PROVIDER_EXTRA = {
    "anthropic": "anthropic",
    "bedrock": "bedrock",
    "vertex": "vertex",
    "mistral": "mistral",
    "google": "google",
    "hindsight": "hindsight",
    "custom": None,
    "openrouter": None,
    "openai": None,
    "litellm": None,
    "local": None,
}

# A configured chat platform -> the extra that ships its client library.
PLATFORM_EXTRA = {
    "telegram": "messaging",
    "discord": "messaging",
    "slack": "messaging",
    "matrix": "matrix",
    "a2a": None,   # in-tree adapter
    "cli": None,
}


def _providers(node):
    """Every `provider:` selection in config.yaml, at any depth (model, aux, memory, fallbacks)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "provider" and isinstance(v, str):
                yield v
            else:
                yield from _providers(v)
    elif isinstance(node, list):
        for v in node:
            yield from _providers(v)


def required_extras(config_text):
    """What the estate's own config.yaml obliges the image to install. Raises on a selection the
    table does not cover: an unknown provider is an unanswerable question, never a pass."""
    cfg = yaml.safe_load(config_text) or {}
    needed = {"otlp"}   # LAW 50: every workload emits, whatever it is configured to talk to
    for name in _providers(cfg):
        if name not in PROVIDER_EXTRA:
            raise AssertionError(
                f"config.yaml selects provider {name!r} and PROVIDER_EXTRA does not know it: "
                "add it with its extra, or with None if it needs no vendor SDK")
        if PROVIDER_EXTRA[name]:
            needed.add(PROVIDER_EXTRA[name])
    for name in (cfg.get("platforms") or {}):
        if name not in PLATFORM_EXTRA:
            raise AssertionError(f"config.yaml enables platform {name!r} and PLATFORM_EXTRA does not know it")
        if PLATFORM_EXTRA[name]:
            needed.add(PLATFORM_EXTRA[name])
    return needed


def missing_extras(config_text, dockerfile_text):
    m = re.search(r"^RUN uv sync .*$", dockerfile_text, re.M)
    assert m, "the uv sync line moved"
    line = m.group(0)
    return sorted(e for e in required_extras(config_text) if f"--extra {e}" not in line)


def test_the_image_installs_an_extra_for_every_provider_the_estate_config_selects():
    """2026-08-28: config.yaml said `model.provider: anthropic`, the sync installed messaging,
    hindsight and otlp, and the pod answered every Telegram DM with `ImportError: The 'anthropic'
    package is required for the Anthropic provider` while reading 1/1 Ready (run 33154124789).
    The list is derived here, so the next provider the founder picks cannot be forgotten."""
    assert missing_extras(open(CONFIG).read(), open(DOCKERFILE).read()) == []


def test_a_sync_that_drops_the_provider_sdk_is_refused():
    """Rung 4 the other way: the exact line that was on main fails against the same config."""
    text = open(DOCKERFILE).read()
    assert missing_extras(open(CONFIG).read(), text.replace(" --extra anthropic", "")) == ["anthropic"]
    assert missing_extras(open(CONFIG).read(), text.replace(" --extra messaging", "")) == ["messaging"]
    assert missing_extras(open(CONFIG).read(), text.replace(" --extra otlp", "")) == ["otlp"]


def test_a_provider_the_table_does_not_know_fails_instead_of_passing():
    """The mistake this file exists to stop is a quiet miss, so an unmapped selection is an error."""
    with pytest.raises(AssertionError, match="does not know it"):
        required_extras("model:\n  provider: some-new-vendor\n")
    with pytest.raises(AssertionError, match="does not know it"):
        required_extras("platforms:\n  whatsapp: {}\n")


def test_an_aggregator_needs_no_vendor_sdk():
    """The router at llm.mumchimp.com is OpenAI-compatible: routing through it must not demand an
    extra, or the model-agnostic path would be the one the image cannot build."""
    assert required_extras("model:\n  provider: custom\n  base_url: https://llm.mumchimp.com/v1\n") == {"otlp"}


def test_dockerignore_keeps_state_and_credentials_out():
    lines = {l.strip() for l in open(DOCKERIGNORE) if l.strip() and not l.startswith("#")}
    for must in (".env", "auth.json", "state.db", "sessions/", "memories/", "hermes-agent/", ".venv/"):
        assert must in lines, must


def test_every_main_image_carries_a_tag_flux_can_order():
    text = open(WORKFLOW).read()
    assert "format('{0}:main-{1}-{2}', env.IMAGE, github.run_number, github.sha)" in text
    push = text.split("  push:", 1)[1].split("permissions:", 1)[0]
    assert "paths:" not in push, "the image carries the whole tree, so every merge to main is a build"


WORKFLOW = os.path.join(os.path.dirname(DOCKERFILE), ".github", "workflows", "build-agent-image.yml")


def sign_step_is_bounded(workflow_text):
    """Run 33110843638: cosign sign hung 5 min until the OIDC token expired. Every sign call is
    under `timeout` and there is a second attempt, so a Sigstore stall costs a retry, not the run."""
    calls = re.findall(r'^\s*(.*)cosign sign --yes', workflow_text, re.M)
    return bool(calls) and all(re.search(r'\btimeout \d+', c) for c in calls) and \
        bool(re.search(r'for attempt in 1 2', workflow_text))


def test_the_sign_step_is_bounded_and_retried():
    assert sign_step_is_bounded(open(WORKFLOW).read())


def test_a_bare_cosign_sign_is_refused():
    assert not sign_step_is_bounded('run: |\n  cosign sign --yes img@sha\n')


def workflow_can_mint_an_id_token(workflow_text):
    """Keyless cosign asks GitHub for an OIDC token; the job needs `id-token: write` or every
    main build dies at 'retrieving ID token' (runs 33101787767, 33110843638)."""
    return bool(re.search(r'^\s+id-token:\s*write\b', workflow_text, re.M))


def test_the_build_can_mint_an_id_token_for_keyless_signing():
    assert workflow_can_mint_an_id_token(open(WORKFLOW).read())


def test_a_workflow_without_id_token_write_is_refused():
    assert not workflow_can_mint_an_id_token("permissions:\n  contents: read\n  packages: write\n")
