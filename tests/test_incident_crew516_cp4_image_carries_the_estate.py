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

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKERFILE = os.path.join(HOME, "Dockerfile")
ENTRYPOINT = os.path.join(HOME, "deploy", "k8s", "entrypoint.sh")
WORKFLOW = os.path.join(HOME, ".github", "workflows", "build-agent-image.yml")
DOCKERIGNORE = os.path.join(HOME, ".dockerignore")


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
    assert "install-cron.py cron/work.jobs  --feature work" in text
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


def test_image_syncs_the_messaging_extra():
    """python-telegram-bot is an extra; a sync without it is a gateway with no Telegram."""
    text = open(DOCKERFILE).read()
    m = re.search(r"^RUN uv sync .*$", text, re.M)
    assert m and "--extra messaging" in m.group(0) and "--extra hindsight" in m.group(0), m and m.group(0)


def test_dockerignore_keeps_state_and_credentials_out():
    lines = {l.strip() for l in open(DOCKERIGNORE) if l.strip() and not l.startswith("#")}
    for must in (".env", "auth.json", "state.db", "sessions/", "memories/", "hermes-agent/", ".venv/"):
        assert must in lines, must


def test_every_main_image_carries_a_tag_flux_can_order():
    text = open(WORKFLOW).read()
    assert "format('{0}:main-{1}-{2}', env.IMAGE, github.run_number, github.sha)" in text
    push = text.split("  push:", 1)[1].split("permissions:", 1)[0]
    assert "paths:" not in push, "the image carries the whole tree, so every merge to main is a build"
