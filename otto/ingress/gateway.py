"""The request pipeline every channel and every customer shares.

Nine steps, in this order, and not one of them names a channel:

1. Find the plugin registered for the channel in the path. Unknown
   channel, 404.
2. Ask the plugin for the credential the request presented. None, 401.
3. Look the customer up by that credential. No row, 401 — deliberately
   the same answer as "no credential", so probing the gateway cannot
   tell a wrong token from an unregistered one.
4. Refuse a connection whose status is not active. 403.
5. Resolve the customer's secret from its reference. Missing, 503: this
   is the platform's fault, not the caller's, and answering 401 would
   send a customer hunting for a token problem they do not have.
6. Verify the presented credential against the resolved secret. Wrong,
   401. Step 3 already matched a fingerprint, so this can only fail if
   the table and the secret store disagree — which is exactly the case
   worth catching rather than trusting the index.
7. Parse the body. Not JSON, or not an object, 400.
8. Normalise through the channel's surface binding, stamped with the
   tenant resolved in step 3. The binding never chooses the tenant.
9. Mint a task envelope and publish it. 202.

An authenticated request whose payload carries nothing to act on — an
empty message, an edit notification, a delivery receipt — is accepted
with 200 and no task. That is not an error: the channel did nothing
wrong, and answering 4xx would make well-behaved platforms retry.

The tenant is on the span, the metrics and every log line from the first
step that knows it, so a trace search can follow one customer through the
whole platform without joining anything by hand.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from otto.ingress.plugins import ChannelPlugin, default_plugins
from otto.ingress.publisher import EventPublisher
from otto.ingress.secrets import SecretNotFound, SecretResolver
from otto.ingress.store import ChannelBindingStore
from otto.obs.core import ObsHandle, TaskContext
from otto.spine.envelope import TaskClass, TaskEnvelope, Tier, TrustTag
from otto.surface.envelope import SurfaceEnvelope, TrustClass

ACCEPTED = 202
NOTHING_TO_DO = 200
BAD_REQUEST = 400
UNAUTHORIZED = 401
FORBIDDEN = 403
NOT_FOUND = 404
UNAVAILABLE = 503

#: A webhook body is a few kilobytes. Anything far past that is either a
#: mistake or an attempt to make the process allocate.
MAX_BODY_BYTES = 1_000_000


@dataclass(frozen=True)
class IngressResult:
    """What one inbound request produced. ``task_id`` and ``tenant_id``
    are present only once the request got far enough to have them, so a
    caller can never read a tenant off a request that failed to
    authenticate."""

    status: int
    reason: str
    tenant_id: str | None = None
    task_id: str | None = None
    subject: str | None = None

    @property
    def body(self) -> bytes:
        payload: dict[str, Any] = {"ok": self.status < BAD_REQUEST}
        if self.reason:
            payload["reason"] = self.reason
        if self.task_id:
            payload["task_id"] = self.task_id
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")


class EventGateway:
    """One instance serves every channel and every customer."""

    def __init__(
        self,
        *,
        store: ChannelBindingStore,
        secrets: SecretResolver,
        publisher: EventPublisher,
        obs: ObsHandle,
        plugins: Mapping[str, ChannelPlugin] | None = None,
    ) -> None:
        self._store = store
        self._secrets = secrets
        self._publisher = publisher
        self._obs = obs
        self._plugins = dict(plugins) if plugins is not None else default_plugins()

    @property
    def channels(self) -> tuple[str, ...]:
        return tuple(sorted(self._plugins))

    def handle(
        self, channel: str, headers: Mapping[str, str], raw_body: bytes
    ) -> IngressResult:
        ctx = TaskContext.new()
        with self._obs.task_span(ctx, "ingress.receive"):
            result = self._handle(channel, headers, raw_body, ctx)
            self._obs.info(
                "ingress.handled",
                # A context carrying the tenant, once it is known, so the
                # log line for a rejected request is not silently
                # attributed to nobody while the accepted one is.
                TaskContext(task_ulid=ctx.task_ulid, tenant_id=result.tenant_id or ""),
                channel=channel,
                status=result.status,
                reason=result.reason,
            )
            return result

    def _handle(
        self,
        channel: str,
        headers: Mapping[str, str],
        raw_body: bytes,
        ctx: TaskContext,
    ) -> IngressResult:
        plugin = self._plugins.get(channel)
        if plugin is None:
            return IngressResult(NOT_FOUND, "unknown channel")

        if len(raw_body) > MAX_BODY_BYTES:
            return IngressResult(BAD_REQUEST, "body too large")

        presented = plugin.present_credential(headers, raw_body)
        if not presented:
            return IngressResult(UNAUTHORIZED, "no credential presented")

        binding = self._store.find_by_credential(channel, presented)
        if binding is None:
            # Same answer as "no credential": an attacker must not be able
            # to tell a registered-but-wrong token from an unknown one.
            return IngressResult(UNAUTHORIZED, "no credential presented")

        if not binding.active:
            return IngressResult(
                FORBIDDEN, "channel connection disabled", tenant_id=binding.tenant_id
            )

        try:
            secret = self._secrets.resolve(binding.secret_ref)
        except SecretNotFound:
            return IngressResult(
                UNAVAILABLE,
                "channel secret unavailable",
                tenant_id=binding.tenant_id,
            )

        if not plugin.verify(presented, secret):
            return IngressResult(UNAUTHORIZED, "no credential presented")

        try:
            native_event = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return IngressResult(
                BAD_REQUEST, "body is not JSON", tenant_id=binding.tenant_id
            )
        if not isinstance(native_event, dict):
            return IngressResult(
                BAD_REQUEST, "body is not a JSON object", tenant_id=binding.tenant_id
            )

        surface_env = plugin.binding().normalize(
            native_event, tenant_id=binding.tenant_id
        )
        content = (surface_env.content or "").strip()
        if not surface_env.is_instruction_bearing or not content:
            return IngressResult(
                NOTHING_TO_DO, "nothing to act on", tenant_id=binding.tenant_id
            )

        task_env = self._mint(surface_env, plugin, content)
        subject = self._publisher.publish_submitted(task_env)
        return IngressResult(
            ACCEPTED,
            "accepted",
            tenant_id=task_env.tenant_id,
            task_id=task_env.task_id,
            subject=subject,
        )

    def _mint(
        self, surface_env: SurfaceEnvelope, plugin: ChannelPlugin, content: str
    ) -> TaskEnvelope:
        """The neutral task the agent lanes will read. It carries the
        channel as provenance only — a lane that changes behaviour on the
        channel name is the coupling this whole package exists to
        prevent."""
        taint = (
            frozenset({TrustTag.untrusted})
            if surface_env.trust_class is TrustClass.UNTRUSTED
            else frozenset()
        )
        return TaskEnvelope(
            task_id=surface_env.correlation_id,
            tenant_id=surface_env.tenant_id,
            source=plugin.task_source,
            **{"class": TaskClass.comms},
            input=content,
            authority_ceiling=Tier.T2,
            context_budget_tokens=24_000,
            cost_budget_usd=0.50,
            deadline_s=600,
            created_at=surface_env.received_at,
            provenance=(
                f"ingress channel:{plugin.channel} "
                f"tenant:{surface_env.tenant_id} "
                f"principal:{surface_env.principal or 'unknown'}"
            ),
            taint=taint,
        )
