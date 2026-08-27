FROM python:3.12-slim AS base

# hermes-agent runtime container — first real build, crew#290/crew#286 (Oracle OKE standby).
# Entry point matches ai.architect.gateway.plist exactly: `python -m hermes_cli.main gateway run`.

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.12.5

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

# Dependency layer already present from the clone above; sync installs it.
RUN uv sync --frozen --no-dev

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
