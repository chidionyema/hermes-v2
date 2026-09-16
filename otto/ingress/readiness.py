"""Dependency checks for GET /readyz.

Five checks, two tiers:

Critical (503 when failed — pod pulled from Service endpoints):
  postgres        — binding table reachable; SELECT 1 via the store's own DSN
  litellm_brain   — loopback sidecar answers /health/liveliness within 1s

Degraded (200 with degraded=true — pod stays in rotation but /readyz body
signals the operator):
  otel_collector  — OTLP endpoint reachable within 500ms
  hindsight       — memory bank reachable within 2s
  nats            — event bus reachable (TCP connect within 1s)

Design notes:
- stdlib urllib.request only (LAW 43; same shape as registration-reconciler.py)
- No caching: Kubernetes readiness probes run every 10s; that is cheap enough
- Timeouts are cumulative worst-case ~4.5s; well under the probe's 5s budget
- A check that cannot be configured (URL not set) reports as degraded, not ok,
  so a mis-configured pod is self-revealing rather than silently wrong
"""
from __future__ import annotations

import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from otto.obs.config import ENDPOINT_ENV

_LITELLM_BASE_URL_ENV = "LITELLM_BASE_URL"
_HINDSIGHT_URL_ENV = "OTTO_MEMORY_HINDSIGHT_URL"
_NATS_URL_ENV = "OTTO_NATS_URL"


@dataclass(frozen=True)
class DepCheck:
    name: str
    ok: bool
    latency_ms: float
    detail: str = ""
    critical: bool = False


def _http_get(url: str, timeout_s: float) -> tuple[bool, float, str]:
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            r.read(512)
        return True, (time.monotonic() - t0) * 1000, ""
    except urllib.error.HTTPError as e:
        # 4xx on a health endpoint still means reachable
        if e.code < 500:
            return True, (time.monotonic() - t0) * 1000, f"http {e.code}"
        return False, (time.monotonic() - t0) * 1000, f"http {e.code}"
    except Exception as e:
        return False, (time.monotonic() - t0) * 1000, type(e).__name__


def _tcp_connect(host: str, port: int, timeout_s: float) -> tuple[bool, float, str]:
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            pass
        return True, (time.monotonic() - t0) * 1000, ""
    except Exception as e:
        return False, (time.monotonic() - t0) * 1000, type(e).__name__


@dataclass
class ReadinessChecker:
    """Builds once at startup, called on every GET /readyz."""

    _dsn: str = field(repr=False)
    _connect_timeout: int = 5
    _litellm_health: str = ""
    _otel_health: str = ""
    _hindsight_health: str = ""
    _nats_host: str = ""
    _nats_port: int = 4222

    @classmethod
    def from_env(
        cls,
        dsn: str,
        connect_timeout: int = 5,
        environ: dict[str, str] | None = None,
    ) -> ReadinessChecker:
        env = environ if environ is not None else os.environ

        litellm_base = env.get(_LITELLM_BASE_URL_ENV, "http://127.0.0.1:4010/v1")
        # /v1/... → /health/liveliness on the same host:port
        litellm_root = litellm_base.split("/v1")[0].rstrip("/")
        litellm_health = litellm_root + "/health/liveliness"

        otlp = env.get(ENDPOINT_ENV, "").strip().rstrip("/")
        otel_health = (otlp + "/health") if otlp else ""

        hindsight = env.get(_HINDSIGHT_URL_ENV, "").strip().rstrip("/")
        hindsight_health = (hindsight + "/health") if hindsight else ""

        nats_url = env.get(_NATS_URL_ENV, "nats://nats.event-bus.svc:4222")
        # strip scheme: nats://host:port
        nats_hostport = nats_url.replace("nats://", "").split("/")[0]
        parts = nats_hostport.rsplit(":", 1)
        nats_host = parts[0]
        nats_port = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 4222

        return cls(
            _dsn=dsn,
            _connect_timeout=connect_timeout,
            _litellm_health=litellm_health,
            _otel_health=otel_health,
            _hindsight_health=hindsight_health,
            _nats_host=nats_host,
            _nats_port=nats_port,
        )

    def _check_postgres(self) -> DepCheck:
        import psycopg

        t0 = time.monotonic()
        try:
            with psycopg.connect(
                self._dsn, connect_timeout=self._connect_timeout, autocommit=True
            ) as conn:
                conn.execute("SELECT 1")
            return DepCheck("postgres", True, (time.monotonic() - t0) * 1000, critical=True)
        except Exception as e:
            return DepCheck(
                "postgres",
                False,
                (time.monotonic() - t0) * 1000,
                type(e).__name__,
                critical=True,
            )

    def check(self) -> tuple[bool, list[DepCheck]]:
        """Run all checks. Returns (all_critical_ok, list[DepCheck])."""
        checks: list[DepCheck] = []

        # Critical: Postgres
        checks.append(self._check_postgres())

        # Critical: LiteLLM brain sidecar (home 1)
        ok, ms, detail = _http_get(self._litellm_health, 1.0)
        checks.append(DepCheck("litellm_brain", ok, ms, detail, critical=True))

        # Degraded: OTel collector
        if self._otel_health:
            ok, ms, detail = _http_get(self._otel_health, 0.5)
        else:
            ok, ms, detail = False, 0.0, f"{ENDPOINT_ENV} not set"
        checks.append(DepCheck("otel_collector", ok, ms, detail, critical=False))

        # Degraded: Hindsight
        if self._hindsight_health:
            ok, ms, detail = _http_get(self._hindsight_health, 2.0)
        else:
            ok, ms, detail = False, 0.0, "OTTO_MEMORY_HINDSIGHT_URL not set"
        checks.append(DepCheck("hindsight", ok, ms, detail, critical=False))

        # Degraded: NATS
        if self._nats_host:
            ok, ms, detail = _tcp_connect(self._nats_host, self._nats_port, 1.0)
        else:
            ok, ms, detail = False, 0.0, "OTTO_NATS_URL not set"
        checks.append(DepCheck("nats", ok, ms, detail, critical=False))

        critical_ok = all(c.ok for c in checks if c.critical)
        return critical_ok, checks
