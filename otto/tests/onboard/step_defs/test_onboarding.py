"""Step definitions for ``features/onboarding.feature`` (W4, crew#768).

Every scenario drives the real ``onboard_command`` entry (the same code
path both CLIs dispatch to), captures its one JSON output line, and
asserts on the world it changed: the gateway registry, the signed
inventory on disk, the Backstage entity file, and the spans in the
in-memory trace backend.
"""

from __future__ import annotations

import io
import json

import yaml
from pytest_bdd import given, parsers, scenarios, then, when

from otto.gateway import Tier, ToolRegistry
from otto.obs.core import COMPONENT_ATTR
from otto.obs.export import obs_test_store
from otto.onboard.cli import onboard_command
from otto.onboard.core import TIER_ATTR, catalog_path_for, inventory_path_for
from otto.spine import inventory
from otto.tests.onboard.conftest import BlindBackend

scenarios("../features/onboarding.feature")

VALID_MANIFEST = {
    "service": "billing-sync",
    "tier": "T1",
    "title": "Billing Sync",
    "description": "Keeps the billing ledger in step with the store's orders.",
    "owner": "group:default/platform",
    "lifecycle": "experimental",
    "tools": [
        {"name": "billing.read", "input_schema": {"type": "object"}},
        {"name": "billing.reconcile", "tier": "T2", "input_schema": {"type": "object"}},
    ],
    "budgets": {"bulk": 1.5},
}


def _write_manifest(manifest_dir, document: dict) -> None:
    manifest_dir.mkdir(parents=True, exist_ok=True)
    path = manifest_dir / f"{document['service']}.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")


def _run(ctx: dict, service: str) -> None:
    buf = io.StringIO()
    registry = ToolRegistry()
    ctx["registry"] = registry
    ctx["exit_code"] = onboard_command(
        service=service,
        registry=registry,
        trace_backend=ctx.get("trace_backend"),
        stdout=buf,
    )
    ctx["output"] = json.loads(buf.getvalue())


# -- Given ---------------------------------------------------------------


@given(parsers.parse('a valid onboarding manifest for "{service}"'))
def valid_manifest(ctx: dict, manifest_dir, service: str) -> None:
    assert VALID_MANIFEST["service"] == service
    _write_manifest(manifest_dir, VALID_MANIFEST)
    ctx["service"] = service


@given(parsers.parse('an onboarding manifest for "{service}" that names no tier'))
def manifest_without_tier(ctx: dict, manifest_dir, service: str) -> None:
    document = {k: v for k, v in VALID_MANIFEST.items() if k != "tier"}
    # The tools that named their own tier keep it; the point under test is
    # the missing SERVICE tier, which nothing may default.
    _write_manifest(manifest_dir, document)
    ctx["service"] = service


@given("a trace backend that cannot see any spans")
def blind_backend(ctx: dict) -> None:
    ctx["trace_backend"] = BlindBackend()


@given("the service was onboarded once already")
def onboarded_once(ctx: dict) -> None:
    _run(ctx, ctx["service"])
    assert ctx["exit_code"] == 0, ctx["output"]
    obs_test_store().clear()


@given("the stored signed inventory was tampered with afterwards")
def tamper_inventory(ctx: dict) -> None:
    path = inventory_path_for(ctx["service"])
    signed = json.loads(path.read_text(encoding="utf-8"))
    # A quiet after-signing privilege escalation: flip a tool's tier row.
    for row in signed["inventory"]["components"]:
        if row["kind"] == "tool":
            row["detail"] = "tier=T3"
            break
    path.write_text(json.dumps(signed, sort_keys=True, indent=2), encoding="utf-8")
    ctx["tampered_text"] = path.read_text(encoding="utf-8")


# -- When ----------------------------------------------------------------


@when(parsers.parse('the operator runs otto onboard for "{service}"'))
def run_onboard(ctx: dict, service: str) -> None:
    _run(ctx, service)


# -- Then: happy path ----------------------------------------------------


@then("the command exits green with a structured outcome")
def exits_green(ctx: dict) -> None:
    assert ctx["exit_code"] == 0, ctx["output"]
    assert ctx["output"]["result"] == "green"
    assert ctx["output"]["service"] == ctx["service"]


@then("every declared tool is registered with the gateway at its explicit tier")
def tools_registered(ctx: dict) -> None:
    registry = ctx["registry"]
    read_spec = registry.get("billing.read")
    reconcile_spec = registry.get("billing.reconcile")
    assert read_spec is not None and reconcile_spec is not None
    # billing.read named no tier of its own, so it takes the tier the
    # manifest EXPLICITLY names for the service (T1) — never a default.
    assert read_spec.tier == Tier.T1
    # billing.reconcile named its own explicit tier.
    assert reconcile_spec.tier == Tier.T2
    assert ctx["output"]["tool_tiers"] == {
        "billing.read": "T1",
        "billing.reconcile": "T2",
    }


@then(
    "the signed capability inventory is on disk and verifies against the onboarding key"
)
def inventory_verifies(ctx: dict) -> None:
    path = inventory_path_for(ctx["service"])
    assert path.exists()
    signed = json.loads(path.read_text(encoding="utf-8"))
    public_key = inventory.load_or_create_keypair().public_key()
    assert inventory.verify_signed_inventory(signed, public_key)
    assert signed["inventory"]["service"] == ctx["service"]
    assert signed["inventory"]["tier"] == "T1"
    kinds = {row["kind"] for row in signed["inventory"]["components"]}
    assert kinds == {"service", "tool", "budget"}


@then("the budget allocations match the manifest")
def budgets_match(ctx: dict) -> None:
    assert ctx["output"]["budgets"] == {"bulk": 1.5}


@then(
    "a Backstage catalog entity file exists with the plain-English "
    "title and description"
)
def catalog_entity_exists(ctx: dict) -> None:
    path = catalog_path_for(ctx["service"])
    assert path.exists()
    entity = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert entity["kind"] == "Component"
    assert entity["metadata"]["name"] == ctx["service"]
    assert entity["metadata"]["title"] == "Billing Sync"
    assert (
        entity["metadata"]["description"]
        == "Keeps the billing ledger in step with the store's orders."
    )
    assert entity["metadata"]["annotations"]["otto.dev/tier"] == "T1"
    assert entity["spec"]["owner"] == "group:default/platform"


@then("the coverage gate saw the service in the trace backend")
def coverage_green(ctx: dict) -> None:
    coverage = ctx["output"]["coverage"]
    assert coverage["result"] == "green"
    (row,) = coverage["components"]
    assert row["component"] == ctx["service"]
    assert row["status"] == "PRESENT"
    assert row["span_count"] > 0


@then("the service's onboarding span carries the service name and the tier")
def span_carries_identity(ctx: dict) -> None:
    spans = [
        span
        for span in obs_test_store().finished_spans()
        if span.resource.attributes.get(COMPONENT_ATTR) == ctx["service"]
    ]
    assert spans, "no span landed in the backend for the onboarded service"
    (span,) = spans
    assert span.resource.attributes.get("service.name") == ctx["service"]
    assert span.attributes[TIER_ATTR] == "T1"


# -- Then: refusals ------------------------------------------------------


def _assert_refused(ctx: dict, step: str) -> dict:
    assert ctx["exit_code"] == 1, ctx["output"]
    output = ctx["output"]
    assert output["error"] == "otto.onboard.refused"
    assert output["result"] == "red"
    assert output["step"] == step
    return output


@then("the command exits red with a structured refusal naming the missing tier")
def refused_no_tier(ctx: dict) -> None:
    output = _assert_refused(ctx, "load_manifest")
    assert "tier" in output["reason"]
    assert "never a privileged one" in output["reason"]


@then("the command exits red with a structured coverage failure")
def refused_coverage(ctx: dict) -> None:
    output = _assert_refused(ctx, "coverage_gate")
    assert output["detail"]["gate"] == "otto-obs-coverage"
    assert output["detail"]["result"] == "red"
    (row,) = output["detail"]["components"]
    assert row["status"] == "ABSENT"


@then(
    parsers.parse(
        'no catalog entity file and no signed inventory exist for "{service}"'
    )
)
def nothing_half_onboarded(service: str) -> None:
    inv_path = inventory_path_for(service)
    cat_path = catalog_path_for(service)
    assert not inv_path.exists()
    assert not cat_path.exists()
    # Rollback means rollback: not even the staged .pending files remain.
    out_dir = inv_path.parent
    leftovers = list(out_dir.glob("*")) if out_dir.exists() else []
    assert leftovers == [], f"half-onboarded artifacts left behind: {leftovers}"


@then("the command exits red with a structured refusal naming the bad signature")
def refused_tampered(ctx: dict) -> None:
    output = _assert_refused(ctx, "verify_prior_inventory")
    assert "does not verify" in output["reason"]
    assert "tampered" in output["reason"]


@then("the tampered inventory file is left in place for investigation")
def tampered_evidence_kept(ctx: dict) -> None:
    path = inventory_path_for(ctx["service"])
    assert path.read_text(encoding="utf-8") == ctx["tampered_text"]
    # And the refusal admitted nothing new: no pending files were promoted.
    assert not list(path.parent.glob("*.pending"))
