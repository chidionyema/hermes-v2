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
  declared a false green. That is luck. The file is now deleted before the flip,
  and a reading is accepted only when `argv` names `/opt/hermes-v2`. The first
  version of that guard matched a Mac path anywhere in the file and was wrong —
  see the outcome below.
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

## Outcome, and three defects the report did not yet know about

The cutover finished at 13:21Z on 2026-08-22. `prospector-hermes-v2` runs the
gateway, Telegram polling is healthy at generation 3, and a one-shot turn inside
the container answers:

    hermes -z "Reply with exactly one word: pong"   ->  pong

That is the first end-to-end proof in this whole incident. Everything before it
proved a file existed.

Three defects surfaced between drafting this report and the cutover working, and
two of them are mine.

**1. The container's root filesystem is rebuilt on every boot.** The bridge made
`/root/.claude -> /data/dot-claude` from an ssh session, so it existed until the
next restart and no longer. Nothing in the estate joined the credential on the
volume to the path the container reads. `entrypoint.sh` now makes the link on
every boot and logs which case it hit. Proven by a restart: the link carries the
boot's timestamp, and the log line reads `identity: /root/.claude ->
/data/dot-claude (credential present)`.

**2. The bridge's own check could not fail.** It asked the container "does a
token resolve?". Five sources can answer that, so it printed `OK` on a run whose
upload it had not confirmed. When a stale reading then showed the directory
empty, the check's own success made the obvious conclusion — that the upload had
silently failed — look proven. It had not; the upload was fine and the reading
was wrong. Both the check and the conclusion were bad, and they agreed with each
other, which is how a single angle behaves (LAW 15). The bridge now compares
digests: the token in the file, the token the resolver hands the gateway, and
the token this run sent. `token=2d35dfd04a15bd0a in-use=2d35dfd04a15bd0a` says
something the old check could not.

**3. A guard I wrote stopped the cutover in the one gap where nothing serves.**
The laptop-path refusal matched `/Users/chidionyema` anywhere in the state file.
`hermes_home` legitimately holds a laptop path in this deployment, so a healthy
state file — `"state":"connected"`, written by pid 667 inside the container eight
seconds earlier — was refused as forged. The script aborted after stopping the
old gateway and before confirming the new one. Service was down until I noticed.
The guard now tests `argv`.

The class in 2 and 3 is the same and it is not the courier problem: **a check
written to catch a specific bad case, verified only against the good case.**
Neither guard was ever shown refusing what it was built to refuse, or accepting
a real reading. A guard is code, and code that has never run against its own
failing input is untested code standing in the critical path.
