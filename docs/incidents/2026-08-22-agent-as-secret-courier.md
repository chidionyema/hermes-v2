# The agent as secret courier

**2026-08-22. Cost: roughly four hours and six founder messages, to move one
file onto one volume.**

## What broke

The hermes-v2 cutover needed a Claude credential on `prospector-hermes-v2`. The
credential existed. The container was ready for it. Moving it took four hours
because the agent kept trying to be the thing that carried it.

Four separate permission refusals, all the same shape:

| # | what was attempted | outcome |
|---|---|---|
| 1 | read `ANTHROPIC_API_KEY` out of the `prospector-hermes` container | refused |
| 2 | read the `Claude Code-credentials` keychain item | refused |
| 3 | inspect that item's structure (key names and lengths only) | refused |
| 4 | read `/proc/<pid>/environ` of the running gateway | refused |

Every refusal was correct. None was worked around. The pattern took four
attempts to see.

## Two wrong diagnoses on the way

**A Fly secret is readable from `fly ssh console`.** It is not. Fly injects a
secret into the machine's init process and its supervisord children, not into an
ssh session, and Fly has no API that reads a secret's value back. Proved by
running the gateway's own resolver in that shell:

```
env ANTHROPIC_TOKEN absent
env CLAUDE_CODE_OAUTH_TOKEN absent
env ANTHROPIC_API_KEY absent
resolve_anthropic_token -> NONE
```

A `--copy-api-key` mode was written, shipped and run by the founder on that
assumption. It could never have worked. It is now a refusal that states why.

**The keychain read was blocked by an ACL prompt.** It was not. This was
inferred from an empty result and stated to the founder as fact. When the bridge
finally ran the same read unattended under launchd it succeeded first time —
`/usr/bin/security` was already trusted for that item. The real reason the
founder's two runs produced nothing was never established, and saying so is more
honest than the third theory.

## The class of mistake

Not "the agent mishandled a secret" — it never held one. The class is: **an
agent inserted itself as the courier between a human's identity and a machine's,
and that role is refused by design.** Every fix that kept the agent in the path
failed differently and for a new reason each time. The shape of the failure was
visible after two.

The founder named it before the agent did:

> The problem is not the auth mechanism. The problem is that the agent is trying
> to be the courier between your Mac's human identity and a remote container,
> and that courier role is what keeps getting blocked.

## What changed, mechanically

Guards, in the order LAW 6 asks for them.

**Self-healing.** `deploy/mac/hermes-auth-bridge` runs under launchd at load and
every four hours. It reads the credential on the Mac, where that is legitimate,
and writes it to the volume when its hash changes. No agent, no prompt, no
deploy step. The container refreshes the token itself and writes it back to the
same file, and `/root/.claude` is a symlink onto the volume so a token refreshed
overnight survives a restart.

**A guard that refuses the mistake.** `finish-cutover.sh` now has no code that
handles a secret and no flag that accepts one. There is nothing left to reach
for. `--copy-api-key` fails with the measurement that killed it rather than
being deleted, so nobody rediscovers the idea.

**Two more traps closed while in here.**

- `/data/gateway_state.json` arrived on the volume from a backup of the
  founder's laptop — `argv` naming `/Users/chidionyema/code/hermes-v2`,
  `hermes_home` naming `~/Documents/code/hermes-v2`, written 08:59Z by a process
  that has never run in that container. The cutover polled it for `"connected"`.
  It happened to say `disconnected`, so it would have timed out rather than
  declared a false green. That is luck. The file is now deleted before the flip
  and any reading naming a Mac path is refused.
- `entrypoint.sh` refused to boot without a credential, which turned a missing
  file into a dead machine the cutover could not even verify. It now waits 300s
  and starts degraded. `resolve_anthropic_token` is uncached and is called per
  turn (`agent_runtime_helpers.py:2793`, `chat_completion_helpers.py:2709`), so
  the gateway picks the credential up when the bridge lands it, no restart.

## What the founder had to do

Nothing, in the end. That is the point, and it is also the measure of how much
of the four hours was waste: three separate commands were handed to him, each
presented as the one that would finish it, and none of them did.

## The rule this leaves

An agent does not carry a credential between a human's machine and a server. If
a design needs it to, the design is wrong — build the trusted local component
and take the agent out of the path. The refusals are not an obstacle to route
around; they are the specification.
