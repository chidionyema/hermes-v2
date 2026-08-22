# syntax=docker/dockerfile:1.7
# hermes-v2 ("The Architect") on Fly.
#
# Modelled on the old estate's proven image (hermes-config deploy/hermes/Dockerfile)
# rather than hermes-agent/Dockerfile upstream. Upstream builds s6-overlay, Node 26,
# Playwright and the web UI -- a 15-45 minute build by its own comment -- and this
# deployment runs none of them. What it needs is the Python agent and nothing else.
#
# Python 3.11 because hermes-agent/pyproject.toml says >=3.11,<3.14 and the venv this
# was developed against is 3.11.15.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/London \
    HERMES_HOME=/opt/hermes-v2

# git: hermes opens pull requests, so it shells out to git and gh.
# ripgrep: the agent's own search path.
# age: decrypts deploy/secrets/claude-credentials.json.age at boot. Debian ships
#   it, so this costs one package and no external download. The doppler CLI is
#   deliberately NOT here — that branch of entrypoint.sh is guarded by
#   `command -v doppler` and stays inert until someone adds it, which is a
#   one-line change on the day it is wanted rather than a vendor apt repo in the
#   image today.
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash git curl ca-certificates tini jq ripgrep procps age \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/hermes-v2

# Dependencies resolve from the manifest alone, so a source-only change does not
# re-resolve 104 packages. The venv lives at .venv because bin/hermes execs
# "$HERMES_HOME/.venv/bin/hermes" by absolute path.
COPY hermes-agent/pyproject.toml hermes-agent/setup.py hermes-agent/uv.lock ./hermes-agent/
RUN python -m venv .venv \
 && ./.venv/bin/pip install --no-cache-dir -e ./hermes-agent

# The editable install above ran before the source arrived, so setuptools wrote its
# finder against a tree with no packages in it. The old estate shipped a broken image
# for exactly this reason -- `python -m hermes_cli.main` died with ModuleNotFoundError
# while the package sat two directories away. Re-link after the copy, and prove the
# import here so a failure lands in the build and not in a 03:00 restart loop.
COPY . .
RUN ./.venv/bin/pip install --no-cache-dir --no-deps -e ./hermes-agent \
 && ./.venv/bin/python -c "import hermes_cli.main; print('hermes_cli is importable')"

COPY deploy/fly/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# tini, because the agent spawns subprocesses (git, gh, rg) that spawn their own and a
# container with no init leaks zombies until the pid table fills.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]

# Nothing polls, nothing writes, nothing is scheduled. CUTOVER.md step 1 is "new estate
# runs WATCH only, old estate off" -- this lands the image with the door shut, and the
# gateway is started deliberately, not by existing.
CMD ["sleep", "infinity"]
