# The Architect

## What it is for

The Architect is your side of the conversation with this estate. You send a message on Telegram, it
reads it, thinks about it with a model, and replies. It also runs five scheduled jobs that reach
you without being asked, so the estate can tell you something happened rather than waiting for you
to come and look. It is the only component that talks to you directly, which is why it being down
is different from anything else being down: everything else can be broken and reported, but if this
is broken nothing gets reported at all.

## What it costs

The model calls are the only meaningful cost, and they are on the cheapest Claude in the price
table by default. A recent turn used 30,366 tokens across four calls. The scheduled jobs are capped
at ninety model calls per turn, which is the ceiling that stops a loop from running up a bill
overnight. The pulse that watches the services alongside it uses no model at all and costs nothing:
it was measured at $85.92 a month when it ran as an agent task, and it was rewritten as plain curl
for that reason.

## What it watches and changes

It watches Telegram for messages from you, and it watches three deployed services: the prospector
store front end, the store API, and the prospector engine. The engine is expected to be switched
off, because you ordered every Fly machine stopped, so that check is inverted and passes on silence.
It changes nothing on its own except its own logs and the queue of jobs it is due to run.

## Where it lives

It runs in the cluster, not on your laptop: Deployment `hermes-agent-gateway` in namespace
`hermes-agent` on OKE, delivered by Flux from `idp/platform/hermes-agent`. The bot token reaches it
from the OCI vault through the External Secrets operator, and Reloader rolls the pod when that entry
changes. The code is at `~/dev/code/hermes-v2` and the image is built from it by CI.

It used to run on this Mac under launchd as `ai.architect.gateway`, and that label is retired along
with its predecessor `ai.hermes.gateway`. Neither plist belongs in `~/Library/LaunchAgents` any
more: one Telegram token admits exactly one poller, so a gateway started here does not join the one
in the cluster, it takes the founder's bot away from it and the cluster pod goes deaf while looking
perfectly healthy. `./bin/verify` fails if either plist is on disk, loaded or not.

## How to turn it off, and back on

Not from here. It is a workload, so it goes up and down the way every workload on this estate does:
a commit to `idp/platform/hermes-agent` that Flux applies within its 10-minute reconcile, or a
break-glass run of `oke-check.yml` if it is an incident. There is deliberately no command in this
document that starts a gateway on this Mac -- printing one is how the second poller kept coming
back (crew#516).

## What goes wrong

The failure that has actually bitten is going deaf: another process picks up the same Telegram
token, Telegram hands each incoming message to whoever asks first, and The Architect stops seeing
your messages while still looking perfectly healthy. It went unnoticed for thirty-one and a half
hours once. The signature is that outbound deliveries keep succeeding in the log while nothing
inbound ever arrives, so a delivery line is not proof that it can hear you.

The second failure is the credential expiring. The gateway keeps running and keeps polling, but
every attempt to think fails. `./bin/verify` catches this at the row that checks an Anthropic
credential exists.

The third is quieter and worth knowing about: a service check that passes for the wrong reason. A
monitor that follows redirects reports green off a login page forever with the app dead behind the
door, so redirects are deliberately not followed here.
