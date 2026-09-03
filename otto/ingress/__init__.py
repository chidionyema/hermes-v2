"""The Universal Event Gateway: one door for every channel, every customer.

Founder directive, 3 September 2026: Otto is an enterprise, multi-tenant,
multi-channel product, not a Telegram bot. Three rules follow from that,
and this package exists to hold all three in one place:

1. **Compute is channel-ignorant.** The agent lanes (spine, gateway,
   verify, memory, router) never learn which channel a message arrived
   on or which customer sent it, beyond the ``tenant_id`` and
   ``source_channel`` fields on the envelope they already read. No pod
   carries a channel environment variable.
2. **One door.** Every inbound webhook, for every channel and every
   customer, arrives at ``POST /webhook/{channel}`` on this service.
   Adding Slack means adding a plugin here, not a route, a pod, a
   manifest or a deployment anywhere.
3. **Onboarding is data, not deployment.** Which customer owns which
   channel lives in the ``channel_binding`` table. Connecting a new
   customer's channel is an insert. Nothing is applied, restarted or
   rolled out, and the very next request routes correctly — proved by
   ``otto/tests/ingress/test_registry_as_data.py``.

Layout, one concern per module:

* ``store`` — the ``channel_binding`` table and the queries over it.
* ``secrets`` — resolving a secret reference to its value. The table
  holds references, never secret material.
* ``plugins`` — per-channel credential extraction and verification. This
  is the *only* place a channel's name appears in a conditional.
* ``gateway`` — the request pipeline shared by every channel.
* ``publisher`` — handing the normalised task to the JetStream spine.
* ``server`` — the socket.
"""

from __future__ import annotations

from otto.obs import instrument
from otto.obs.core import ObsHandle

COMPONENT = "ingress"


def boot() -> ObsHandle:
    """Instrument this component, or refuse to start. Same boot contract
    every other Otto lane follows: a component that cannot emit does not
    run dark, it does not run."""
    return instrument(COMPONENT)
