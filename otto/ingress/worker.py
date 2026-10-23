"""The other half of the one door: answering what the door accepted.

``otto.ingress.gateway`` ends at ``202 Accepted``: the task envelope is
on the bus and the socket is free. Something has to take it off again,
answer it, and talk back to the customer on the channel they used. That
is this module, and until it existed the unified door was one-way — a
message could be accepted from any channel and no answer could ever
reach anyone, because nothing subscribed to ``OTTO_TASKS`` and the
envelope carried no address to reply to.

Three things make it one messaging layer rather than a second one:

* **One answering path.** It calls ``otto.boot.pipeline.answer_envelope``
  — the same function the legacy Telegram webhook lane calls — so an
  answer cannot depend on which door the message came in through.
* **One address book.** The reply credential is resolved from the same
  ``channel_binding`` row that authenticated the request, through
  ``outbound_secret_ref``. Connecting a customer stays a database write,
  in both directions; nothing about a customer reaches a deployment.
* **One place a channel means anything.** The address is opaque here and
  is handed back to the plugin that minted it (``send_reply``). This
  module never learns what a chat id is.

Delivery discipline, because a work queue that retries the wrong things
is worse than none: a task that can never succeed as written — no reply
address on a channel that can only push, no binding for its tenant, a
channel that cannot be pushed to and cannot be read back from — is
terminated, not redelivered, and the reason is logged. A task this lane
can answer but not deliver to (``ChannelPlugin.PULLABLE``: the caller
reads the answer back by task id) is answered and acknowledged, and the
log says ``worker.answered_undelivered`` rather than pretending a reply
went out. Anything that could succeed on a second attempt — the model
router, Telegram's API, the database — is negatively acknowledged so the
server redelivers it.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Mapping

from otto.boot.pipeline import (
    ObsHandles,
    answer_envelope,
    boot_obs_handles,
    build_registry,
)
from otto.gateway.core import ToolGateway, fail_closed_gate
from otto.ingress.plugins import OutboundNotSupported, default_plugins
from otto.ingress.secrets import SecretNotFound, SecretResolver
from otto.ingress.store import ChannelBindingStore
from otto.obs.core import ObsHandle, TaskContext
from otto.router.providers import ProviderClient
from otto.spine import subjects
from otto.spine.bus import Bus
from otto.spine.envelope import TaskEnvelope

#: One durable name for the whole answering lane. Every replica pulls
#: from it, so a task goes to exactly one of them and a replica that dies
#: mid-answer has its task redelivered to another (``Bus.durable_pull``
#: is explicit-ack for precisely this).
DURABLE = "otto-answer"

#: The subject a freshly accepted task lands on.
SUBMITTED = subjects.task_subject(subjects.TaskState.submitted)
STREAM = subjects.StreamName.OTTO_TASKS.value

BATCH = 8
FETCH_TIMEOUT_S = 5.0


#: Telegram clears its typing indicator after about five seconds
#: (otto.boot.presence keeps the same cadence on the boot path).
TYPING_REFRESH_S = 4.0


class _Typing:
    """The typing indicator's lifetime.

    ``set()`` stops the keepalive **and waits for the thread to exit**. The
    earlier event-only version left a window: the thread could be about to
    call ``send_chat_action`` when the answer went out, so "the indicator
    stops once the reply is out" was true eventually rather than at the
    moment the reply was delivered -- measured 2026-10-23 as a red
    ``test_the_typing_indicator_shows_while_the_answer_is_being_written``
    on gates run 40091280499 (one run in three, same tree). A courtesy that
    can outlive the answer it announces is a leak, and a test that can only
    see it sometimes is a test that fails for the wrong reason.
    """

    __slots__ = ("_event", "_thread")

    def __init__(
        self, event: threading.Event, thread: threading.Thread | None = None
    ) -> None:
        self._event = event
        self._thread = thread

    def set(self) -> None:
        self._event.set()
        if self._thread is not None:
            self._thread.join(timeout=TYPING_REFRESH_S + 1.0)


def _no_typing() -> _Typing:
    """The keepalive for a task with nowhere to show one: already stopped.

    The answering lane always calls ``set()`` in a ``finally``, so the no-op
    has to be a stopped handle rather than ``None`` -- a None there would
    turn "this surface has no typing indicator" into an ``AttributeError``
    on the path that answers it.
    """
    done = threading.Event()
    done.set()
    return _Typing(done)


def _typing_keepalive(plugin: Any, secret: str, reply_to: str) -> _Typing:
    """Show "typing" on ``plugin`` until the returned handle is set.

    A plugin without ``send_chat_action`` (HTTP) gets no thread. The first
    refused call ends the courtesy quietly: presence never costs an answer.
    """
    done = threading.Event()
    action = getattr(plugin, "send_chat_action", None)
    if not callable(action):
        done.set()
        return _Typing(done)

    def _run() -> None:
        while not done.is_set():
            try:
                action(secret, reply_to)
            except Exception:  # noqa: BLE001 - a courtesy may never raise
                return
            if done.wait(TYPING_REFRESH_S):
                return

    thread = threading.Thread(target=_run, name="otto-typing", daemon=True)
    thread.start()
    return _Typing(done, thread)


class Worker:
    """Pull submitted tasks, answer them, reply on the customer's channel."""

    def __init__(
        self,
        *,
        bus: Bus,
        store: ChannelBindingStore,
        secrets: SecretResolver,
        obs: ObsHandle,
        lanes: ObsHandles,
        gateway: ToolGateway,
        plugins: Mapping[str, Any] | None = None,
        provider_client: ProviderClient | None = None,
    ) -> None:
        self._bus = bus
        self._store = store
        self._secrets = secrets
        self._obs = obs
        self._lanes = lanes
        self._gateway = gateway
        self._plugins = dict(plugins) if plugins is not None else default_plugins()
        self._provider_client = provider_client
        self._sub = None

    async def subscribe(self) -> None:
        self._sub = await self._bus.durable_pull(
            stream=STREAM,
            durable=DURABLE,
            filter_subject=SUBMITTED,
            # Everything already on the stream, not only what arrives
            # after this replica started: a task accepted while no worker
            # was running is a customer waiting, not a task to skip.
            deliver_all=True,
        )

    async def run_once(self) -> int:
        """Fetch one batch and answer it. Returns how many were handled.

        An empty fetch is the normal quiet case and returns 0 rather than
        raising: ``nats-py`` times out when a work queue has nothing on
        it, which is not an error condition.
        """
        if self._sub is None:
            await self.subscribe()
        try:
            batch = await self._sub.fetch(BATCH, timeout=FETCH_TIMEOUT_S)
        except (TimeoutError, asyncio.TimeoutError):
            return 0
        for msg in batch:
            await self._handle(msg)
        return len(batch)

    async def run_forever(self) -> None:
        await self.subscribe()
        while True:
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001 - a loop that dies stops answering
                self._obs.info("worker.loop_error", TaskContext.new(), error=str(exc))
                await asyncio.sleep(1)

    async def _handle(self, msg) -> None:
        try:
            envelope = TaskEnvelope.model_validate_json(msg.data)
        except Exception as exc:  # noqa: BLE001 - a bad envelope never parses
            # Terminated, not redelivered: bytes that are not an envelope
            # will not become one on a second delivery, and left to retry
            # they would block the queue behind them forever.
            self._obs.info("worker.unparseable", TaskContext.new(), error=str(exc))
            await msg.term()
            return

        ctx = TaskContext(task_ulid=envelope.task_id, tenant_id=envelope.tenant_id)
        channel = envelope.source.value

        plugin = self._plugins.get(channel)
        if plugin is None:
            self._obs.info("worker.unknown_channel", ctx, channel=channel)
            await msg.term()
            return

        # A pullable surface (``ChannelPlugin.PULLABLE``) does not need an
        # address an answer is *pushed* to: the task id is the address, and
        # the door reads the answer back on it. Before this distinction
        # existed the two checks below ran first and terminated the task, so
        # an accepted ``api`` turn never reached the router at all --
        # measured 2026-10-23 as ``worker.no_reply_address`` on the first
        # turn this surface ever carried.
        pullable = bool(getattr(plugin, "PULLABLE", False))
        if not envelope.reply_to and not pullable:
            self._obs.info("worker.no_reply_address", ctx, channel=channel)
            await msg.term()
            return

        # The bot the message came in on answers. Two bots can serve one
        # tenant on one channel (founder 2026-09-05: both Telegram bots must
        # answer), and a tenant lookup would send every reply through
        # whichever row came first. Envelopes minted before the door stamped
        # the binding fall back to the tenant lookup.
        if envelope.reply_binding:
            binding = self._store.find_by_external_id(channel, envelope.reply_binding)
        else:
            binding = self._store.find_by_tenant(channel, envelope.tenant_id)

        # Delivery is optional; answering is not. A binding with an outbound
        # reference gives this lane an address and a credential to push to.
        # A pullable surface needs neither: the answer is written against the
        # task id by the answering path and read back over the door.
        delivery_secret: str | None = None
        if binding is not None and binding.outbound_secret_ref:
            try:
                delivery_secret = self._secrets.resolve(binding.outbound_secret_ref)
            except SecretNotFound as exc:
                # The platform's fault, and a fixable one: retry.
                self._obs.info("worker.secret_unavailable", ctx, error=str(exc))
                await msg.nak()
                return
        elif not pullable:
            # A listen-only connection. Not a fault of this delivery, and
            # not something a retry fixes: the operator adds the outbound
            # reference to the row, and the next message answers.
            self._obs.info(
                "worker.no_outbound_binding",
                ctx,
                channel=channel,
                bound=binding is not None,
            )
            await msg.term()
            return

        # The founder could not tell whether Otto was answering or down
        # (2026-09-06): the boot path shows Telegram's typing indicator
        # (otto.boot.presence) but this lane, the one every Telegram message
        # actually takes, showed nothing for the tens of seconds a reasoning
        # lane needs. The indicator lives as long as the answer does, and
        # only where there is a place to show it.
        typing_done = (
            _typing_keepalive(plugin, delivery_secret, envelope.reply_to)
            if delivery_secret is not None and envelope.reply_to
            else _no_typing()
        )
        try:
            answer = answer_envelope(
                envelope,
                registry_gateway=self._gateway,
                obs=self._lanes,
                provider_client=self._provider_client,
            )
        except Exception as exc:  # noqa: BLE001 - the model or a lane failed
            self._obs.info("worker.answer_failed", ctx, error=str(exc))
            await msg.nak()
            return
        finally:
            typing_done.set()

        if not answer.reply_text:
            # The gateway denied the task, or there was nothing to say.
            # Acknowledged: the task was handled, and silence is the
            # designed answer to an unauthorised sender.
            self._obs.info("worker.no_reply", ctx, channel=channel)
            await msg.ack()
            return

        if delivery_secret is None or not envelope.reply_to:
            # Answered, and readable at the door by task id, but there is no
            # address on this channel to push it to. Acknowledged rather
            # than terminated: the answer exists and was recorded, so a
            # redelivery would only produce it twice. Named so that a turn
            # nobody read is visible in the log instead of looking answered.
            self._obs.info(
                "worker.answered_undelivered",
                ctx,
                channel=channel,
                pullable=pullable,
            )
            await msg.ack()
            return

        try:
            plugin.send_reply(delivery_secret, envelope.reply_to, answer.reply_text)
        except OutboundNotSupported as exc:
            if pullable:
                # The answer is recorded and fetchable; the push was never
                # going to exist on this channel.
                self._obs.info(
                    "worker.answered_undelivered",
                    ctx,
                    channel=channel,
                    pullable=True,
                    error=str(exc),
                )
                await msg.ack()
                return
            self._obs.info("worker.outbound_unsupported", ctx, error=str(exc))
            await msg.term()
            return
        except Exception as exc:  # noqa: BLE001 - the channel's API refused
            self._obs.info("worker.send_failed", ctx, error=str(exc))
            await msg.nak()
            return

        # Step 3: the inbound was speech, so the answer goes out as a voice
        # note too (ADR 0022). Best-effort, never a retry trigger: the text
        # reply above already reached the customer, and a door that cannot
        # speak (no wired speaker) must not cost them their answer.
        if envelope.wants_voice_reply:
            send_voice = getattr(plugin, "send_voice", None)
            if callable(send_voice):
                try:
                    send_voice(delivery_secret, envelope.reply_to, answer.reply_text)
                except OutboundNotSupported as exc:
                    self._obs.info("worker.voice_unsupported", ctx, error=str(exc))
                except Exception as exc:  # noqa: BLE001 - audio upload refused
                    self._obs.info("worker.voice_failed", ctx, error=str(exc))

        self._obs.info("worker.answered", ctx, channel=channel)
        await msg.ack()


def start_worker_thread(
    *,
    store: ChannelBindingStore,
    secrets: SecretResolver,
    obs: ObsHandle,
) -> threading.Thread:
    """Run the answering lane beside the socket, in this same process.

    Its own event loop and its own NATS connection, deliberately: the
    gateway's loop is driven synchronously by ``JetStreamPublisher`` from
    whichever request thread is publishing, and a consumer that needs the
    loop to be *running* cannot share it. One extra connection to the
    same server is cheaper than either making the whole door asynchronous
    or adding a second deployment for the sake of the second half of one
    conversation.
    """

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        bus = loop.run_until_complete(Bus().connect())
        loop.run_until_complete(bus.ensure_streams())
        worker = Worker(
            bus=bus,
            store=store,
            secrets=secrets,
            obs=obs,
            lanes=boot_obs_handles(),
            gateway=ToolGateway(registry=build_registry(), human_gate=fail_closed_gate),
        )
        loop.run_until_complete(worker.run_forever())

    thread = threading.Thread(target=_run, name="otto-answer", daemon=True)
    thread.start()
    return thread
