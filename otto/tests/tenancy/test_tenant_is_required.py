"""An envelope that cannot say whose message it is gets refused.

The alternative — a default tenant — is the failure this whole change
exists to prevent: one customer's message quietly processed, stored and
billed under another customer's name, with nothing in the trace to show
it happened.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from otto.obs import instrument
from otto.obs.config import MODE_ENV, MODE_TEST
from otto.obs.core import TENANT_ATTR, ULID_ATTR, TaskContext
from otto.obs.export import obs_test_store
from otto.spine.envelope import TaskClass, TaskEnvelope, TaskSource, Tier
from otto.surface.envelope import Capability, SurfaceEnvelope, TrustClass

TENANT = "tenant-acme"


def _surface(**overrides) -> SurfaceEnvelope:
    fields = {
        "tenant_id": TENANT,
        "surface": "telegram",
        "principal": None,
        "trust_class": TrustClass.UNTRUSTED,
        "capabilities": frozenset({Capability.TEXT}),
        "content": "hello",
        "received_at": datetime.now(timezone.utc),
    }
    fields.update(overrides)
    return SurfaceEnvelope(**fields)


# -- the surface envelope -------------------------------------------------


def test_a_surface_envelope_with_a_tenant_is_built_normally() -> None:
    assert _surface().tenant_id == TENANT


def test_a_surface_envelope_cannot_be_built_without_a_tenant() -> None:
    with pytest.raises(TypeError):
        SurfaceEnvelope(  # type: ignore[call-arg]
            surface="telegram",
            principal=None,
            trust_class=TrustClass.UNTRUSTED,
            capabilities=frozenset({Capability.TEXT}),
            content="hello",
            received_at=datetime.now(timezone.utc),
        )


@pytest.mark.parametrize("blank", ["", "   ", None, 0])
def test_a_surface_envelope_refuses_a_blank_or_non_text_tenant(blank) -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        _surface(tenant_id=blank)


# -- the task envelope ----------------------------------------------------


def _task(**overrides) -> TaskEnvelope:
    fields = {
        "tenant_id": TENANT,
        "source": TaskSource.telegram,
        "task_class": TaskClass.comms,
        "input": "hello",
        "authority_ceiling": Tier.T1,
        "provenance": "test",
    }
    fields.update(overrides)
    return TaskEnvelope.new(**fields)


def test_a_task_envelope_cannot_be_minted_without_naming_a_tenant() -> None:
    """Not a default, not a validation message after the fact: the call
    does not compile into a working call without the customer."""
    with pytest.raises(TypeError, match="tenant_id"):
        TaskEnvelope.new(  # type: ignore[call-arg]
            source=TaskSource.telegram,
            task_class=TaskClass.comms,
            input="hello",
            authority_ceiling=Tier.T1,
            provenance="test",
        )


def test_a_task_envelope_refuses_a_blank_tenant() -> None:
    with pytest.raises(ValidationError, match="tenant_id"):
        _task(tenant_id="")


def test_a_task_envelope_read_off_the_wire_refuses_a_missing_tenant() -> None:
    """The bus is the boundary that matters: an envelope from another
    process is refused if it cannot say whose work it is."""
    payload = _task().model_dump(mode="json", by_alias=True)
    del payload["tenant_id"]
    with pytest.raises(ValidationError, match="tenant_id"):
        TaskEnvelope.model_validate(payload)


def test_a_task_envelope_carries_its_tenant_through_serialisation() -> None:
    """The bus moves canonical JSON, so a tenant that survives only in
    memory would be lost at the first hop."""
    restored = TaskEnvelope.model_validate_json(_task().canonical_json())
    assert restored.tenant_id == TENANT


# -- the trace ------------------------------------------------------------


@pytest.fixture
def _test_obs(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(MODE_ENV, MODE_TEST)
    obs_test_store().clear()
    yield
    obs_test_store().clear()


def test_the_tenant_labels_the_span_and_the_log_line(_test_obs) -> None:
    """One search on the customer's id must return their work and nobody
    else's — which is only true if the label is on the span itself, not
    on the process."""
    obs = instrument("tenancy-test")
    ctx = TaskContext.new(tenant_id=TENANT)

    with obs.task_span(ctx, "tenancy.check"):
        obs.info("tenancy.checked", ctx)

    span = obs_test_store().finished_spans()[-1]
    assert span.attributes[TENANT_ATTR] == TENANT

    line = obs_test_store().log_lines[-1]
    assert line[TENANT_ATTR] == TENANT
    assert line[ULID_ATTR] == ctx.task_ulid


def test_work_that_belongs_to_no_customer_carries_no_tenant_label(
    _test_obs,
) -> None:
    """A blank label would read as a customer whose id is the empty
    string, which is a worse claim than no label at all."""
    obs = instrument("tenancy-test")
    ctx = TaskContext.new()

    with obs.task_span(ctx, "tenancy.check"):
        obs.info("tenancy.checked", ctx)

    span = obs_test_store().finished_spans()[-1]
    assert TENANT_ATTR not in span.attributes
    assert TENANT_ATTR not in obs_test_store().log_lines[-1]
