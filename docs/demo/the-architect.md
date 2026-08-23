# The Architect, running

The Architect is the thing that reads your Telegram messages, thinks about them with a model, and
answers you. This page shows it doing all three, with the commands that produced the output above
each block. Nothing here is typed by hand; every block is pasted from a real run on 2026-08-23.

## Does it work? One command answers

```
cd ~/dev/code/hermes-v2 && ./bin/verify
```

```
Verifying /Users/chidionyema/dev/code/hermes-v2

  PASS  estate.yaml describes an estate    prospector 3
  PASS  generated files match templates    27 checked
  PASS  README describes what ships        27 generated, 7 jobs, 127 tracked
  PASS  the agent runs                     Hermes Agent v0.20.5
  PASS  agent is the pinned commit         1220c4ad24 (want 1220c4ad24)
  PASS  install and agent agree on python  install: >=3.11,<3.14  agent: >=3.11,<3.14
  PASS  the venv python is in range        3.11
  PASS  agent home is this directory       /Users/chidionyema/dev/code/hermes-v2/hermes-agent
  PASS  every model has a price            Cheapest Claude in the table: claude-3-h
  PASS  an Anthropic credential exists     auth.json (hermes auth login)
  PASS  .env is private (mode 600)         mode 600
  PASS  no secrets tracked in git
        engine       000   stopped, as ordered  https://prospector-engine.fly.dev/health
        store-web    200   answering  https://prospector-store-web.fly.dev/
        store-api    404   answering  https://prospector-store-api.fly.dev/
  PASS  every service is as ordered
  PASS  cron jobs installed                5 jobs
  PASS  the gateway is running             cron ticker alive
  PASS  every job reaches the founder      all jobs deliver off-machine
  PASS  launchd runs the plist on disk     loaded definition matches the file

  17 passed, 0 failed
```

Read the `engine` row carefully, because it is the one that looks wrong and is not. You ordered
every Fly machine stopped. That service returns nothing at all, and nothing at all is the correct
answer, so the check is inverted for it: silence passes and an answer would fail. If that row ever
says `AWAKE, AND SHOULD NOT BE`, a machine woke up and started spending money nobody authorised.

## It hears you

Seventeen green rows prove the machinery is assembled. They do not prove the gateway is listening
right now, so that is measured separately, from the live process rather than from a log a dead
poller would leave behind unchanged.

```
lsof -nP -p 20054 -a -i TCP | grep ESTABLISHED
dig +short api.telegram.org
```

```
python3.1 20054 chidionyema   20u  IPv4 TCP 192.168.0.192:64986->149.154.166.110:443 (ESTABLISHED)
python3.1 20054 chidionyema   27u  IPv4 TCP 192.168.0.192:59614->149.154.166.110:443 (ESTABLISHED)

149.154.166.110
```

Two open connections to the address Telegram answers on, held by the gateway process itself. That
is a long poll waiting for your next message. The log agrees from the other side:

```
[Telegram] Telegram polling confirmed healthy: getUpdates progressing (generation 327)
```

## It thinks

```
grep 'API call' logs/agent.log | tail -1
```

```
agent.conversation_loop: API call #4: model=claude-haiku-4-5 provider=anthropic
in=30272 out=94 total=30366 latency=2.5s
Turn ended: reason=text_response(finish_reason=stop) model=claude-haiku-4-5 api_calls=4/90
```

A real model call, on the cheap model, four calls against a ceiling of ninety.

## It answers

```
grep 'delivered to telegram' logs/agent.log | tail -1
```

```
cron.scheduler: Job '55c67a9f8513': delivered to telegram:<your chat id> via live adapter
```

That is a message that left this machine and arrived on your phone. The word `live` matters: the
adapter has a rehearsal mode that logs a delivery without sending one, and this is not it.
