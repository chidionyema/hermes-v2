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

from otto.ingress.plugins import (
    ChannelPlugin,
    OutboundNotSupported,
    default_plugins,
)
from otto.ingress.media import MediaEnrichment, detect_media
from otto.ingress.publisher import EventPublisher
from otto.ingress.secrets import SecretNotFound, SecretResolver
from otto.ingress.store import ChannelBinding, ChannelBindingStore
from otto.ingress.widgets import (
    FounderEvidence,
    WidgetQuery,
    detect_widget_query,
    founder_authorized,
    is_brief_query,
)
from otto.memory import conversation
from otto.memory.conversation import StoreUnavailable
from otto.obs.core import ObsHandle, TaskContext
from otto.spine.envelope import TaskClass, TaskEnvelope, Tier, TrustTag
from otto.surface.envelope import SurfaceEnvelope, TrustClass

OK = 200
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
    #: Extra fields for a read-back response (``answer_for``). Absent on the
    #: accept path, where the body is the three keys a caller needs.
    payload: dict[str, Any] | None = None

    @property
    def body(self) -> bytes:
        payload: dict[str, Any] = {"ok": self.status < BAD_REQUEST}
        if self.reason:
            payload["reason"] = self.reason
        if self.task_id:
            payload["task_id"] = self.task_id
        if self.payload:
            payload.update(self.payload)
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
        media: "MediaEnrichment | None" = None,
        widgets: "FounderEvidence | None" = None,
    ) -> None:
        self._store = store
        self._secrets = secrets
        self._publisher = publisher
        self._obs = obs
        self._plugins = dict(plugins) if plugins is not None else default_plugins()
        #: The media seam that turns voice/photo into words (steps 3 and
        #: 4). ``None`` means a text-only door: a media update whose only
        #: reachable content is audio/pixels is gated as nothing-to-do,
        #: exactly as before this capability existed.
        self._media = media
        #: The evidence seam that populates the founder widget's brief. A
        #: door with no seam (the default in a bare checkout) answers a
        #: widget tap with a fail-closed "no evidence" packet — never
        #: invented numbers.
        self._widgets = widgets

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

        # A native message widget tap is its own update kind and its own
        # deadline: Telegram drops the tap if ``answerWidgetQuery`` is not
        # sent within 15 seconds, so it is handled here, synchronously, on
        # the authenticated credential's own connection — never pushed to
        # the async answering lane, which would be tens of seconds late.
        widget_query = detect_widget_query(native_event)
        if widget_query is not None:
            return self._handle_widget_query(widget_query, plugin, binding)

        # /office is a transport action, not an LLM prompt. The existing
        # channel credential was authenticated above; authenticate the
        # founder's sender and private chat separately before any response.
        office_result = self._handle_office_command(native_event, plugin, binding)
        if office_result is not None:
            return office_result

        # The allow-list travels with the customer's row, so who counts as
        # a named principal on this channel is a database fact rather than
        # a deployment fact.
        surface_env = plugin.binding(binding.principal_allowlist).normalize(
            native_event, tenant_id=binding.tenant_id
        )
        # Step 3 (voice) and step 4 (photo): a media update whose only
        # reachable content is a voice note or pixels is still a message the
        # door can answer, so media is sensed before the text gate. ``message``
        # is the same dict the binding normalised; the seam is given the
        # already-resolved bot token (the one place network + fork live, and
        # exactly the token ``verify`` above proved). A door with no media
        # seam stays text-only: a media-only update it cannot sense is nothing
        # to act on, as before.
        media_content: str | None = None
        wants_voice_reply = False
        if self._media is not None:
            message = native_event.get("message", native_event)
            if isinstance(message, dict) and detect_media(message).is_media:
                try:
                    enriched = self._media.process(message, secret)
                    media_content = enriched.content.strip()
                    wants_voice_reply = enriched.wants_voice_reply
                except Exception as exc:  # noqa: BLE001 - sensing refused/missing
                    # A media the door cannot sense (the transcriber/describer
                    # is down, the file will not fetch) is not a message this
                    # delivery can act on, and retrying the webhook will not
                    # fix it: terminate the request quietly rather than loop.
                    return IngressResult(
                        UNAVAILABLE,
                        f"media could not be sensed: {type(exc).__name__}",
                        tenant_id=binding.tenant_id,
                    )
        content = (
            media_content if media_content is not None else (surface_env.content or "")
        ).strip()
        if not surface_env.is_instruction_bearing or not content:
            return IngressResult(
                NOTHING_TO_DO, "nothing to act on", tenant_id=binding.tenant_id
            )

        task_env = self._mint(
            surface_env,
            plugin,
            content,
            binding.external_id,
            wants_voice_reply=wants_voice_reply,
        )
        try:
            subject = self._publisher.publish_submitted(task_env)
        except Exception as exc:  # noqa: BLE001 - the bus refused the task
            # A turn that never reached the bus has not been accepted, and
            # saying 202 would be the lie that cost four days: measured
            # 2026-10-23, the last task on OTTO_TASKS was published
            # 2026-10-19T12:08:26Z, the pod stayed Ready, and every turn
            # after that was dropped with no line in the log at all. 503 is
            # also the answer that makes the channel retry.
            self._obs.info(
                "ingress.publish_failed",
                TaskContext(task_ulid=task_env.task_id, tenant_id=task_env.tenant_id),
                channel=channel,
                error=type(exc).__name__,
            )
            return IngressResult(
                UNAVAILABLE,
                "the task bus refused the work; the turn was not accepted",
                tenant_id=task_env.tenant_id,
                task_id=task_env.task_id,
            )
        return IngressResult(
            ACCEPTED,
            "accepted",
            tenant_id=task_env.tenant_id,
            task_id=task_env.task_id,
            subject=subject,
        )

    def answer_for(self, headers: Mapping[str, str], task_id: str) -> IngressResult:
        """The answer to a turn this tenant asked, read back by task id.

        This is the other half of a pullable surface (``HttpPlugin``): the
        202 hands the caller a task id and there is no address to push an
        answer to, so the caller fetches it here with the same credential it
        sent the turn with.

        Who is asking is decided by the credential alone -- the channel is
        whichever plugin accepts the presented one, so the read-back has no
        per-channel path either. A turn belonging to another tenant is
        reported as absent rather than forbidden: the reader already proved
        which tenant it is, and telling it that someone else's turn exists
        would leak the customer list one ULID at a time.
        """
        tenant_id: str | None = None
        authenticated = False
        for plugin in self._plugins.values():
            presented = plugin.present_credential(headers, b"")
            if not presented:
                continue
            binding = self._store.find_by_credential(plugin.channel, presented)
            if binding is None or not binding.active:
                continue
            try:
                secret = self._secrets.resolve(binding.secret_ref)
            except SecretNotFound:
                continue
            if not plugin.verify(presented, secret):
                continue
            authenticated = True
            tenant_id = binding.tenant_id
            break
        if not authenticated:
            return IngressResult(UNAUTHORIZED, "no credential presented")

        try:
            turn = conversation.read_turn(task_id)
        except StoreUnavailable as exc:
            return IngressResult(
                UNAVAILABLE, f"conversation store unavailable: {exc}", tenant_id
            )
        except Exception as exc:  # noqa: BLE001 - the driver, on a read path
            return IngressResult(
                UNAVAILABLE,
                f"conversation store failed: {type(exc).__name__}",
                tenant_id,
            )
        if turn is None or turn.tenant_id != tenant_id:
            return IngressResult(NOT_FOUND, "no such answered turn", tenant_id)
        return IngressResult(
            OK,
            "answered",
            tenant_id=tenant_id,
            task_id=turn.task_ulid,
            payload=turn.to_payload(),
        )

    def _handle_office_command(
        self, native_event: dict[str, Any], plugin: ChannelPlugin, binding: ChannelBinding
    ) -> IngressResult | None:
        """Deliver actual Telegram widget HTML, never queue /office as a model turn."""
        if plugin.channel != "telegram":
            return None
        message = native_event.get("message")
        if not isinstance(message, dict):
            return None
        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if command != "/office":
            return None

        chat = message.get("chat")
        sender = message.get("from")
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        sender_id = sender.get("id") if isinstance(sender, dict) else None
        allowlist = binding.principal_allowlist
        founder = (
            chat is not None and chat.get("type") == "private"
            and type(chat_id) is int and chat_id > 0
            and type(sender_id) is int and sender_id == chat_id
            and allowlist.get(str(chat_id)) == "founder"
            and allowlist.get(str(sender_id)) == "founder"
        )
        if not founder:
            return IngressResult(FORBIDDEN, "office widget founder-only", tenant_id=binding.tenant_id)
        if not binding.outbound_secret_ref:
            return IngressResult(UNAVAILABLE, "office widget outbound credential absent",
                                 tenant_id=binding.tenant_id)
        try:
            token = self._secrets.resolve(binding.outbound_secret_ref)
        except SecretNotFound:
            return IngressResult(UNAVAILABLE, "office widget credential unavailable",
                                 tenant_id=binding.tenant_id)
        send = getattr(plugin, "send_widget", None)
        if not callable(send):
            return IngressResult(UNAVAILABLE, "office widget unsupported on this channel",
                                 tenant_id=binding.tenant_id)
        try:
            message_id = send(
                token, str(chat_id), self._safe_brief(), "otto_founder_compass"
            )
            if type(message_id) is not int or message_id <= 0:
                return IngressResult(UNAVAILABLE, "office widget send has no Telegram message id",
                                     tenant_id=binding.tenant_id)
        except Exception:
            # Never surface the token-bearing HTTP exception in the gateway.
            self._obs.info("ingress.office_send_failed", TaskContext.new(tenant_id=binding.tenant_id))
            return IngressResult(UNAVAILABLE, "office widget delivery failed",
                                 tenant_id=binding.tenant_id)
        self._obs.info(
            "ingress.office_widget_sent", TaskContext.new(tenant_id=binding.tenant_id),
            message_id=message_id,
        )
        return IngressResult(
            OK, "office widget delivered", tenant_id=binding.tenant_id,
            payload={"message_id": message_id},
        )

    def _handle_widget_query(
        self,
        query: WidgetQuery,
        plugin: ChannelPlugin,
        binding: ChannelBinding,
    ) -> IngressResult:
        """The founder tapped the private-office widget: answer it now.

        Fail-closed in three layers, in order of cheapness: a non-``brief``
        tap is ignored (the widget only ever sends ``brief``); a tap whose
        sender and view chat are not both the founder is refused with no
        answer; and a tap whose outbound credential is missing resolves to
        a 503 rather than a silent drop. Auth decisions name the tenant so
        the log line is attributable.
        """
        if not is_brief_query(query):
            return IngressResult(
                NOTHING_TO_DO, "widget query not brief", tenant_id=binding.tenant_id
            )
        if not founder_authorized(query, binding.principal_allowlist):
            self._obs.info(
                "ingress.widget_refused",
                TaskContext.new(tenant_id=binding.tenant_id),
                reason="not founder on this channel",
            )
            return IngressResult(
                FORBIDDEN, "widget query not authorized", tenant_id=binding.tenant_id
            )

        if not binding.outbound_secret_ref:
            return IngressResult(
                UNAVAILABLE, "no outbound widget credential", tenant_id=binding.tenant_id
            )
        try:
            outbound_secret = self._secrets.resolve(binding.outbound_secret_ref)
        except SecretNotFound:
            return IngressResult(
                UNAVAILABLE,
                "widget credential unavailable",
                tenant_id=binding.tenant_id,
            )

        answer = getattr(plugin, "answer_widget_query", None)
        if not callable(answer):
            return IngressResult(
                UNAVAILABLE,
                "widget not supported on this channel",
                tenant_id=binding.tenant_id,
            )

        brief = self._safe_brief()
        try:
            answer(outbound_secret, query.id, brief)
        except OutboundNotSupported:
            return IngressResult(
                UNAVAILABLE,
                "widget not supported on this channel",
                tenant_id=binding.tenant_id,
            )
        except Exception as exc:  # noqa: BLE001 - the channel API refused
            self._obs.info(
                "ingress.widget_answer_failed",
                TaskContext.new(tenant_id=binding.tenant_id),
                error=str(exc),
            )
            return IngressResult(
                UNAVAILABLE,
                "widget answer failed",
                tenant_id=binding.tenant_id,
            )

        self._obs.info(
            "ingress.widget_answered",
            TaskContext.new(tenant_id=binding.tenant_id),
        )
        return IngressResult(
            NOTHING_TO_DO, "widget query answered", tenant_id=binding.tenant_id
        )

    def _safe_brief(self) -> dict[str, Any]:
        """The ``chat_data`` object the widget renders. A down or absent
        evidence source yields a fail-closed "no verified evidence" packet
        in the same shape, never invented numbers and never an exception
        that would lose the 15-second window."""
        if self._widgets is None:
            return {
                "r": 0,
                "at": 0,
                "headline": "No verified estate briefing is available.",
                "delivered": None,
                "active": None,
                "issues": None,
                "priority": "—",
                "id": "—",
                "proof": "—",
            }
        try:
            return self._widgets.brief()
        except Exception as exc:  # noqa: BLE001 - a down source is a stale brief
            self._obs.info(
                "ingress.widget_evidence_down",
                TaskContext.new(),
                error=str(exc),
            )
            return {
                "r": 0,
                "at": 0,
                "headline": "Verified evidence source is unavailable right now.",
                "delivered": None,
                "active": None,
                "issues": None,
                "priority": "—",
                "id": "—",
                "proof": "—",
            }

    def _mint(
        self,
        surface_env: SurfaceEnvelope,
        plugin: ChannelPlugin,
        content: str,
        reply_binding: str | None = None,
        *,
        wants_voice_reply: bool = False,
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
            # Carried, not interpreted. The gateway's own docstring says a
            # lane must not branch on the channel name; an opaque address
            # minted by the channel's binding and handed back to the same
            # binding to send keeps that true while still closing the loop.
            reply_to=surface_env.reply_to,
            reply_binding=reply_binding,
            # Step 3: the answering lane needs to know this inbound was
            # speech, so its reply is sent as audio as well as text (ADR
            # 0022). Carried opaquely, like ``reply_to``.
            wants_voice_reply=wants_voice_reply,
        )
