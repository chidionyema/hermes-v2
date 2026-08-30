FROM python:3.12-slim AS base

# hermes-agent runtime container — first real build, crew#290/crew#286 (Oracle OKE standby).
# Entry point matches ai.architect.gateway.plist exactly: `python -m hermes_cli.main gateway run`.

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates openssh-client netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.12.5

# crew#561: every estate skill (estate-map, incident-triage, pr-discipline, phone-idea-flow) tells
# the agent to run `gh`, and the pod had no `gh` -- so Otto told the founder he had "no access to
# GitHub" while GITHUB_TOKEN sat in his env (gh reads GH_TOKEN/GITHUB_TOKEN, no login step).
# Pinned release binary, arch-aware: the image builds on ubuntu-24.04-arm, the cluster runs arm64.
ARG GH_VERSION=2.98.0
RUN arch="$(dpkg --print-architecture)" \
    && curl --retry 5 -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${arch}.tar.gz" \
       | tar -xz -C /usr/local --strip-components=1 "gh_${GH_VERSION}_linux_${arch}/bin/gh" \
    && gh --version

WORKDIR /app

# hermes-agent is NousResearch's separate upstream repo, not this repo's own source
# (it's gitignored here -- .gitignore:44,50 -- ./install fetches it fresh at the pin).
# The pinned commit lives on the chidionyema/hermes-agent FORK, not upstream NousResearch
# -- confirmed via the local checkout's `fork` remote; NousResearch/hermes-agent refused
# the fetch with "not our ref" (real CI failure, run 33051856843). Mirrors ./install's
# own shallow-fetch-and-checkout, against the same fork it actually uses.
COPY PINNED_VERSION ./
RUN git clone --quiet --no-checkout https://github.com/chidionyema/hermes-agent.git hermes-agent \
    && cd hermes-agent \
    && git fetch --quiet --depth 1 origin "$(sed -n 2p ../PINNED_VERSION)" \
    && git checkout --quiet "$(sed -n 2p ../PINNED_VERSION)" \
    && rm -rf .git

WORKDIR /app/hermes-agent

# Dependency layer already present from the clone above; sync installs it. Telegram is the
# `messaging` extra, not a base dependency (pyproject: python-telegram-bot under [messaging]); a
# sync without it boots a gateway that cannot open the founder's chat. `hindsight` is the memory
# client (config.yaml memory.provider: hindsight); `otlp` is the trace exporter (LAW 50);
# `anthropic` is the SDK config.yaml model.provider names. Its absence is what made the cluster
# gateway a green pod that serves nobody: 1/1 Ready, Telegram polling, and every DM answered
# with `ImportError: The 'anthropic' package is required for the Anthropic provider` from
# anthropic_adapter.py:866 (2026-08-28 06:32Z, 07:37Z, 07:39Z; run 33154124789 read it out of
# the pod). Third instance of one mistake, so the rule replaces the list: every provider
# config.yaml selects has its extra here, and the test derives that from config.yaml rather
# than repeating a hand-written set that the next selection will again fall out of.
# uv sync runs as root and installs the managed CPython the fork pins under
# $HOME/.local/share/uv/python, i.e. /root (0700). The pod runs as 10001 with a read-only root
# filesystem, so .venv/bin/python -> /root/... was "Permission denied" at entrypoint.sh:52 and the
# gateway crash-looped on the cluster (crew#516 CP4, image main-13-a367cbd, proved with
# `crane export --platform linux/arm64 ... | tar -tv`). The interpreter lives in a world-readable
# directory instead.
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python
# crew#717 wave 1: `edge-tts` is the free default voice (pyproject pins 7.2.7) and `langfuse`
# is the observability plugin's SDK (not a pyproject extra, so pinned here; the plugin no-ops
# without credentials, so the SDK alone changes nothing off-cluster).
RUN uv sync --frozen --no-dev --extra messaging --extra hindsight --extra otlp --extra anthropic --extra edge-tts \
    && uv pip install --no-cache langfuse==3.15.0 \
    && chmod -R a+rX /opt/uv \
    && test -x "$(readlink -f .venv/bin/python)" \
    && case "$(readlink -f .venv/bin/python)" in /root/*) echo "python under /root" >&2; exit 1;; esac

ENV PATH="/app/hermes-agent/.venv/bin:$PATH"
ENV HERMES_HOME=/app
ENV PYTHONUNBUFFERED=1

# The estate itself -- config.yaml, SOUL.md, skills/, scripts/, bin/, templates/, cron/*.jobs --
# rides in the image at /app/estate (crew#516 CP4). Without it the container is upstream
# hermes-agent with no personality, no skills and no lanes: not The Architect. .dockerignore keeps
# every state path and every credential out (LAW 21, LAW 46); deploy/k8s/entrypoint.sh copies the
# build over the persistent HERMES_HOME volume at boot and leaves the state on it alone.
COPY --chown=10001:10001 . /app/estate

# No secrets baked in: .env values arrive as environment from a Secret the platform syncs
# (External Secrets Operator, crew#227 CP3); auth.json is seeded once from HERMES_AUTH_JSON.
ENV HERMES_HOME=/data

EXPOSE 9900

ENTRYPOINT ["/app/estate/deploy/k8s/entrypoint.sh"]
