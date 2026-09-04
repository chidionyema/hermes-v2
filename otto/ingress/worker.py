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
address, no binding for its tenant, a channel that cannot be pushed to —
is terminated, not redelivered, and the reason is logged. Anything that
could succeed on a second attempt — the model router, Telegram's API,
the database — is negatively acknowledged so the server redelivers it.
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
from otto.gateway.core import ToolGateway
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

        if not envelope.reply_to:
            self._obs.info("worker.no_reply_address", ctx, channel=channel)
            await msg.term()
            return

        plugin = self._plugins.get(channel)
        if plugin is None:
            self._obs.info("worker.unknown_channel", ctx, channel=channel)
            await msg.term()
            return

        binding = self._store.find_by_tenant(channel, envelope.tenant_id)
        if binding is None or not binding.outbound_secret_ref:
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

        if not answer.reply_text:
            # The gateway denied the task, or there was nothing to say.
            # Acknowledged: the task was handled, and silence is the
            # designed answer to an unauthorised sender.
            self._obs.info("worker.no_reply", ctx, channel=channel)
            await msg.ack()
            return

        try:
            secret = self._secrets.resolve(binding.outbound_secret_ref)
        except SecretNotFound as exc:
            # The platform's fault, and a fixable one: retry.
            self._obs.info("worker.secret_unavailable", ctx, error=str(exc))
            await msg.nak()
            return

        try:
            plugin.send_reply(secret, envelope.reply_to, answer.reply_text)
        except OutboundNotSupported as exc:
            self._obs.info("worker.outbound_unsupported", ctx, error=str(exc))
            await msg.term()
            return
        except Exception as exc:  # noqa: BLE001 - the channel's API refused
            self._obs.info("worker.send_failed", ctx, error=str(exc))
            await msg.nak()
            return

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
            gateway=ToolGateway(registry=build_registry()),
        )
        loop.run_until_complete(worker.run_forever())

    thread = threading.Thread(target=_run, name="otto-answer", daemon=True)
    thread.start()
    return thread
