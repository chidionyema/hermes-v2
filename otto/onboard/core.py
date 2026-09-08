"""The onboarding engine: six steps, each reusing the platform layer that owns it.

Reuse map (headline rule — one platform layer each, never a copy):

- tool registration: ``otto.gateway.ToolRegistry`` / ``ToolSpec``;
- inventory signing: ``otto.spine.inventory`` (CP1's Ed25519 machinery —
  same key custody, same canonical-JSON signing, same verify);
- budget allocation: ``otto.router.config.RouterConfig.from_policy_dict``
  plus ``otto.router.budget.BudgetLedger``;
- trace stamping: ``otto.obs.instrument``;
- coverage proof: ``otto.obs.coverage.check_coverage`` against the trace
  backend (LAW 50: the backend is queried, files are never scanned).

Fail closed, nothing half-onboarded: the catalog entity and the signed
inventory are first written as ``.pending`` files, and only PROMOTED to
their real names after the coverage gate has seen the service in the
backend. A red gate deletes the pending files and raises a structured
refusal — the estate never holds artifacts for a service that was not
actually admitted.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from otto.gateway import (
    DuplicateTool,
    SchemaViolation,
    Tier,
    ToolCapacityExceeded,
    ToolRegistry,
    ToolSpec,
)
from otto.obs import ObsBootError, ObsConfig, TaskContext, instrument
from otto.obs.config import MODE_TEST
from otto.obs.core import COMPONENT_ATTR
from otto.obs.coverage import NO_BACKEND_REASON, TraceBackend, check_coverage
from otto.onboard.catalog import catalog_yaml
from otto.onboard.errors import OnboardingRefused
from otto.onboard.manifest import OnboardingManifest
from otto.router.budget import BudgetLedger
from otto.router.config import RouterConfig
from otto.spine import inventory

#: Span attribute carrying the service's authority tier. The service name
#: itself rides as the OpenTelemetry resource ``service.name`` (set by
#: ``otto.obs.instrument``); the tier is stamped here per span.
TIER_ATTR = "otto.tier"

ONBOARD_SPAN_NAME = "otto.onboard"


def default_onboard_dir() -> Path:
    """LAW 46: never a hardcoded location. ``OTTO_ONBOARD_DIR`` wins, then
    the estate state dir, then the caller's own home as the dev fallback
    (the same ladder ``otto.spine.inventory.default_key_path`` climbs)."""
    raw = os.environ.get("OTTO_ONBOARD_DIR")
    if raw:
        return Path(raw)
    state_dir = Path(os.environ.get("OTTO_STATE_DIR", Path.home() / ".otto"))
    return state_dir / "onboard"


def inventory_path_for(service: str, output_dir: Path | None = None) -> Path:
    return (output_dir or default_onboard_dir()) / f"{service}.inventory.json"


def catalog_path_for(service: str, output_dir: Path | None = None) -> Path:
    return (output_dir or default_onboard_dir()) / f"{service}.catalog-info.yaml"


@dataclass(frozen=True, slots=True)
class OnboardingOutcome:
    """The green result — one JSON-ready record of what was admitted."""

    service: str
    tier: str
    tools_registered: tuple[str, ...]
    tool_tiers: dict[str, str]
    budgets: dict[str, float]
    inventory_path: str
    catalog_path: str
    signature_key_id: str
    coverage: dict

    def as_dict(self) -> dict[str, object]:
        out = asdict(self)
        out["tools_registered"] = list(self.tools_registered)
        out["result"] = "green"
        out["command"] = "otto onboard"
        return out


def build_service_inventory(
    manifest: OnboardingManifest,
    budgets: dict[str, float],
    *,
    generated_at: datetime | None = None,
) -> dict:
    """The service's capability inventory — generated from the validated
    manifest, never hand-typed, in the same component-row shape CP1's
    inventory uses so one diff/verify toolchain reads both."""
    ts = generated_at or datetime.now(timezone.utc)
    rows = [
        inventory.Component(
            kind="service",
            name=manifest.service,
            version="v1",
            detail=f"tier={manifest.tier.name} owner={manifest.owner}",
        )
    ]
    for tool in manifest.tools:
        rows.append(
            inventory.Component(
                kind="tool",
                name=tool.name,
                version="v1",
                detail=f"tier={tool.tier.name}",
            )
        )
    for lane, amount in budgets.items():
        rows.append(
            inventory.Component(
                kind="budget",
                name=lane,
                version="v1",
                detail=f"daily_budget_usd={amount}",
            )
        )
    rows.sort(key=lambda c: (c.kind, c.name))
    return {
        "generated_at": ts.isoformat(),
        "service": manifest.service,
        "tier": manifest.tier.name,
        "components": [asdict(c) for c in rows],
    }


class _TestStoreBackend:
    """Test-mode ``TraceBackend``: query the in-memory store the obs
    layer exports to under ``OTTO_OBS_MODE=test`` — still a query of the
    backend the spans actually landed in, never a scan of files."""

    def span_count(self, component: str, window_seconds: float) -> int:
        from otto.obs.export import obs_test_store

        return sum(
            1
            for span in obs_test_store().finished_spans()
            if span.resource.attributes.get(COMPONENT_ATTR) == component
        )


def _resolve_backend(
    trace_backend: TraceBackend | None, config: ObsConfig
) -> TraceBackend | None:
    if trace_backend is not None:
        return trace_backend
    if config.mode == MODE_TEST:
        return _TestStoreBackend()
    return None


def _register_tools(manifest: OnboardingManifest, registry: ToolRegistry) -> None:
    try:
        for tool in manifest.tools:
            registry.register(
                ToolSpec(
                    name=tool.name,
                    tier=tool.tier,
                    input_schema=dict(tool.input_schema),
                )
            )
    except (DuplicateTool, SchemaViolation, ToolCapacityExceeded) as exc:
        raise OnboardingRefused(
            manifest.service,
            "register_tools",
            f"the gateway refused a declared tool: {exc}",
            "fix the tool declaration in the manifest and run otto onboard again",
        ) from exc


def _allocate_budgets(
    manifest: OnboardingManifest,
) -> tuple[RouterConfig, dict[str, float]]:
    policy = {
        "lanes": {lane: {} for lane in manifest.budgets},
        "guards": {"daily_budget_usd": dict(manifest.budgets)},
    }
    try:
        config = RouterConfig.from_policy_dict(policy)
    except (ValueError, KeyError, TypeError) as exc:
        raise OnboardingRefused(
            manifest.service,
            "allocate_budgets",
            f"the router refused the budget allocation: {exc}",
            "name only lanes the router knows (for example judgment, "
            "bulk, verify) with non-negative daily USD amounts",
        ) from exc
    allocated = {lane: config.lanes[lane].daily_budget_usd for lane in manifest.budgets}
    ledger = BudgetLedger(config)
    for lane, amount in allocated.items():
        if amount > 0 and ledger.exhausted(lane):
            raise OnboardingRefused(
                manifest.service,
                "allocate_budgets",
                f"lane {lane!r} was allocated {amount} USD but the ledger "
                "reports it exhausted before any spend — the allocation "
                "did not take",
            )
    return config, allocated


def _verify_prior_inventory(manifest: OnboardingManifest, inv_path: Path, key) -> None:
    if not inv_path.exists():
        return
    try:
        prior = json.loads(inv_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OnboardingRefused(
            manifest.service,
            "verify_prior_inventory",
            f"the stored signed inventory at {inv_path} is unreadable "
            f"({exc}); refusing to overwrite it",
            "investigate the file, then remove it deliberately if it is beyond repair",
        ) from exc
    if not inventory.verify_signed_inventory(prior, key.public_key()):
        raise OnboardingRefused(
            manifest.service,
            "verify_prior_inventory",
            f"the stored signed inventory at {inv_path} does not verify "
            "against the onboarding key — it was tampered with after "
            "signing, or signed by a different key; refusing to overwrite "
            "the evidence",
            "investigate how the artifact changed; a capability diff "
            "without an approved change is an incident",
        )


def onboard_service(
    manifest: OnboardingManifest,
    *,
    registry: ToolRegistry | None = None,
    output_dir: Path | None = None,
    key_path: Path | None = None,
    trace_backend: TraceBackend | None = None,
    obs_config: ObsConfig | None = None,
) -> OnboardingOutcome:
    """Run all six steps; return the green outcome or raise a structured
    ``OnboardingRefused`` with nothing left half-onboarded."""
    registry = registry if registry is not None else ToolRegistry()
    _register_tools(manifest, registry)
    _router_config, allocated = _allocate_budgets(manifest)

    out_dir = output_dir or default_onboard_dir()
    inv_path = inventory_path_for(manifest.service, out_dir)
    cat_path = catalog_path_for(manifest.service, out_dir)

    key = inventory.load_or_create_keypair(key_path)
    _verify_prior_inventory(manifest, inv_path, key)

    signed = inventory.sign_inventory(build_service_inventory(manifest, allocated), key)
    if not inventory.verify_signed_inventory(signed, key.public_key()):
        raise OnboardingRefused(
            manifest.service,
            "sign_inventory",
            "the freshly signed inventory does not verify against its own "
            "key — the signing round trip failed",
        )

    config = obs_config if obs_config is not None else ObsConfig.from_env()
    try:
        handle = instrument(manifest.service, config)
    except ObsBootError as exc:
        raise OnboardingRefused(
            manifest.service,
            "stamp_traces",
            "the service could not be instrumented, so it would run dark; "
            f"onboarding refuses: {exc.reason}",
            exc.remedy,
            detail=exc.as_dict(),
        ) from exc

    try:
        ctx = TaskContext.new()
        with handle.task_span(ctx, ONBOARD_SPAN_NAME) as span:
            span.set_attribute(TIER_ATTR, manifest.tier.name)
            handle.info(
                "service.onboarding",
                ctx,
                service=manifest.service,
                tier=manifest.tier.name,
                tools=[t.name for t in manifest.tools],
            )

        # Stage the artifacts; promotion waits for the coverage gate.
        out_dir.mkdir(parents=True, exist_ok=True)
        pending: list[tuple[Path, Path]] = []
        for final_path, content in (
            (inv_path, json.dumps(signed, sort_keys=True, indent=2)),
            (cat_path, catalog_yaml(manifest)),
        ):
            staged = final_path.with_suffix(final_path.suffix + ".pending")
            staged.write_text(content, encoding="utf-8")
            pending.append((staged, final_path))

        backend = _resolve_backend(trace_backend, config)
        report = None
        if backend is not None:
            report = check_coverage(
                [manifest.service], backend, config.coverage_window_seconds
            )
        if backend is None or report.red:
            for staged, _final in pending:
                staged.unlink(missing_ok=True)
            if backend is None:
                raise OnboardingRefused(
                    manifest.service,
                    "coverage_gate",
                    f"{NO_BACKEND_REASON}; onboarding cannot prove the "
                    "service is visible, so it is not admitted",
                    "bind a trace backend (SigNoz at integration, or "
                    "OTTO_OBS_MODE=test in suites)",
                )
            raise OnboardingRefused(
                manifest.service,
                "coverage_gate",
                "the coverage gate cannot see this service in the trace "
                "backend — not visible means not admitted; the staged "
                "catalog entity and signed inventory were rolled back",
                "check the exporter endpoint and the collector, then run "
                "otto onboard again",
                detail=report.as_dict(),
            )

        for staged, final_path in pending:
            os.replace(staged, final_path)
    finally:
        # In test mode the handle exports through the PROCESS-SHARED
        # in-memory store; shutting the providers down would stop that
        # shared exporter for every component instrumented after this one
        # (InMemorySpanExporter refuses all exports once stopped, and the
        # store's clear() does not revive it). Real mode shuts down
        # cleanly; test mode leaves the shared store running.
        if config.mode != MODE_TEST:
            handle.shutdown()

    return OnboardingOutcome(
        service=manifest.service,
        tier=manifest.tier.name,
        tools_registered=tuple(t.name for t in manifest.tools),
        tool_tiers={t.name: t.tier.name for t in manifest.tools},
        budgets=allocated,
        inventory_path=str(inv_path),
        catalog_path=str(cat_path),
        signature_key_id=signed["key_id"],
        coverage=report.as_dict(),
    )


__all__ = [
    "ONBOARD_SPAN_NAME",
    "TIER_ATTR",
    "OnboardingOutcome",
    "Tier",
    "build_service_inventory",
    "catalog_path_for",
    "default_onboard_dir",
    "inventory_path_for",
    "onboard_service",
]
