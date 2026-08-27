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

# No secrets baked in — .env / config.yaml are runtime-mounted (External Secrets Operator,
# crew#227 CP3), never COPYed into the image (LAW 21, LAW 46).

EXPOSE 9900

ENTRYPOINT ["python", "-m", "hermes_cli.main"]
CMD ["gateway", "run"]
