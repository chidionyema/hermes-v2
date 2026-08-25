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

The code is at `~/dev/code/hermes-v2`. It runs under launchd as `ai.architect.gateway`, defined by
`~/Library/LaunchAgents/ai.architect.gateway.plist`. Logs are in `logs/agent.log` next to the code.
There is an older label called `ai.hermes.gateway` from the retired estate, and it must stay
unloaded; if you ever see both running, they will fight over the same Telegram token and one of them
will go deaf.

## How to turn it off

```
launchctl bootout gui/$(id -u)/ai.architect.gateway
```

It stops immediately and stays stopped across a reboot. Nothing else on the estate depends on it
being up, so nothing else breaks when you do this. You stop getting messages, which is the point.

## How to turn it back on

```
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.architect.gateway.plist
```

Then confirm it came back with `cd ~/dev/code/hermes-v2 && ./bin/verify`, which should print
`17 passed, 0 failed`.

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
