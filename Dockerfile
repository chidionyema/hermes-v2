FROM python:3.12-slim AS base

# hermes-agent runtime container — first real build, crew#290/crew#286 (Oracle OKE standby).
# Entry point matches ai.architect.gateway.plist exactly: `python -m hermes_cli.main gateway run`.

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.12.5

WORKDIR /app/hermes-agent

# Dependency layer first for cache efficiency: uv.lock rarely changes as often as source.
COPY hermes-agent/pyproject.toml hermes-agent/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Now the real source.
COPY hermes-agent/ ./

RUN uv sync --frozen --no-dev

ENV PATH="/app/hermes-agent/.venv/bin:$PATH"
ENV HERMES_HOME=/app
ENV PYTHONUNBUFFERED=1

# No secrets baked in — .env / config.yaml are runtime-mounted (External Secrets Operator,
# crew#227 CP3), never COPYed into the image (LAW 21, LAW 46).

EXPOSE 9900

ENTRYPOINT ["python", "-m", "hermes_cli.main"]
CMD ["gateway", "run"]
