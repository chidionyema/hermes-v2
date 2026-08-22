# How Hermes gets Claude, and why that broke on Fly

Written 2026-08-22, after the cutover stalled on it. Every link below is pinned to
`NousResearch/hermes-agent` at [`fcbd1076a93841fa88855acce810e342a5b78101`](https://github.com/NousResearch/hermes-agent/blob/fcbd1076a93841fa88855acce810e342a5b78101) (tag `v2026.8.19`), which is the
commit `hermes-agent/` is checked out at in this tree. Pinned, so the line numbers stay true.

## It does not shell out to `claude`

There is no `claude -p` anywhere on the inference path. Hermes talks to `api.anthropic.com`
itself and *presents* as Claude Code:

- [`anthropic_adapter.py:953`](https://github.com/NousResearch/hermes-agent/blob/fcbd1076a93841fa88855acce810e342a5b78101/agent/anthropic_adapter.py#L953) —
  `"user-agent": f"claude-code/{_get_claude_code_version()} (external, cli)"`
- [`anthropic_adapter.py:414`](https://github.com/NousResearch/hermes-agent/blob/fcbd1076a93841fa88855acce810e342a5b78101/agent/anthropic_adapter.py#L414) —
  `_detect_claude_code_version()` runs `claude --version` for the sole purpose of filling in
  that string

The only `subprocess` call to a `claude` binary in the whole tree is
[`run_oauth_setup_token()`](https://github.com/NousResearch/hermes-agent/blob/fcbd1076a93841fa88855acce810e342a5b78101/agent/anthropic_adapter.py#L1486), which runs
`claude setup-token` to mint a credential. That is a login, not an inference call.

## The five places it looks for a token, in order

[`resolve_anthropic_token()`, `anthropic_adapter.py:1428`](https://github.com/NousResearch/hermes-agent/blob/fcbd1076a93841fa88855acce810e342a5b78101/agent/anthropic_adapter.py#L1428):

| # | source | kind | survives a move to a server? |
|---|---|---|---|
| 1 | `ANTHROPIC_TOKEN` env var | OAuth / setup token | **yes** |
| 2 | `CLAUDE_CODE_OAUTH_TOKEN` env var | Claude Code setup-token | **yes** |
| 3 | `ANTHROPIC_API_KEY` env var | metered API key | yes, but it bills per token |
| 4 | Claude Code credentials on disk or in the Keychain | subscription OAuth | **no** |
| 5 | `auth.json` credential-pool OAuth entry | subscription OAuth | no — see below |

Note that **2 outranks 3**. A setup-token beats an API key, so putting one on a server puts that
server on the subscription rather than on metered billing.

## What was actually happening on the laptop

Source #4. [`credential_sources.py:6`](https://github.com/NousResearch/hermes-agent/blob/fcbd1076a93841fa88855acce810e342a5b78101/agent/credential_sources.py#L6) names it:

    claude_code   — ~/.claude/.credentials.json

On this Mac that file is a stale leftover — its `expiresAt` decodes to `2026-08-05T04:00:06Z`,
seventeen days dead. The live read is the Keychain, at
[`_read_claude_code_credentials_from_keychain()`, `anthropic_adapter.py:1025`](https://github.com/NousResearch/hermes-agent/blob/fcbd1076a93841fa88855acce810e342a5b78101/agent/anthropic_adapter.py#L1025):

```sh
security find-generic-password -s "Claude Code-credentials" -w
```

Claude Code >= 2.1.114 moved storage there. So Hermes was borrowing the founder's Claude Code
login out of the macOS login Keychain and spending his subscription. No API key, nothing metered.

## Why it could not travel to Fly

Two reasons, and the second is the one that surprises people.

**There is no Keychain and no `~/.claude` in the container.** Source #4 resolves to nothing.

**`auth.json` deliberately does not carry the token.** The disk boundary strips it.
[`credential_persistence.py:20`](https://github.com/NousResearch/hermes-agent/blob/fcbd1076a93841fa88855acce810e342a5b78101/agent/credential_persistence.py#L20) lists the only
provider/source pairs whose secrets are allowed onto disk:

```python
_PERSISTABLE_PROVIDER_SOURCES = frozenset({
    ("anthropic", "hermes_pkce"),
    ("minimax-oauth", "oauth"),
    ("nous", "device_code"),
    ("openai-codex", "device_code"),
    ("xai-oauth", "device_code"),
})
```

`claude_code` is not in that set, so everything else "is treated as borrowed/reference-only by
default so future external secret providers fail closed at the disk boundary". Our entry reads:

```
anthropic  auth_type='oauth'  source='claude_code'  fingerprint=yes  has_access_token=False
```

A fingerprint and no token. Copying `auth.json` to the server — which we did, hash-verified —
moves the *record* of the credential and none of the credential. That is the trap: the file looks
like it worked.

Meanwhile every call the gateway has ever made went to Anthropic:

```
$ sqlite3 state.db "select model, billing_provider, count(*) from session_model_usage group by 1,2"
claude-haiku-4-5|anthropic|...
```

So losing source #4 does not degrade the gateway. It stops it.

## The fix

Use source #2. `claude setup-token` mints a long-lived token for exactly this case:

```sh
claude setup-token                                                  # interactive OAuth, prints a token
./deploy/fly/set-claude-token.sh prospector-hermes-v2               # paste it; never echoed
```

**Do not use the Keychain's `accessToken` for a server.** It is short-lived, and refresh needs the
refresh token plus [`_prefer_refreshable_claude_code_token`](https://github.com/NousResearch/hermes-agent/blob/fcbd1076a93841fa88855acce810e342a5b78101/agent/anthropic_adapter.py#L1428)
finding a credentials file that does not exist in the container. The gateway would work and then
die hours later. A setup-token is the one built to be long-lived.

## Related traps in the same area

- `.env` here has `ANTHROPIC_API_KEY=` **empty**. There is nothing local to copy.
- The other three pool entries are all `source=env:*` — `OPENROUTER_API_KEY` (absent, and
  `last_status=exhausted`), `GITHUB_TOKEN`, `GEMINI_API_KEY`. Those do travel, because a Fly
  secret becomes an env var and
  [`get_env_value`, `config.py:4451`](https://github.com/NousResearch/hermes-agent/blob/fcbd1076a93841fa88855acce810e342a5b78101/hermes_cli/config.py#L4451) reads `os.environ` before
  `.env`.
- `prospector-engine` has no Anthropic credential of any kind. It never used Claude.

## Finishing the cutover without handing a secret to an agent

Three separate permission-classifier denials stopped this session from touching
the credential at all: reading `ANTHROPIC_API_KEY` out of the old container,
reading the Claude Code entry out of the login Keychain, and inspecting that
entry's structure. Those refusals are correct — an agent has no business holding
a credential — so the move is done by a script the founder runs, which never
prints the value.

`deploy/fly/finish-cutover.sh` has two credential modes.

### `--copy-api-key` — withdrawn; it could never have worked

The idea was to copy `ANTHROPIC_API_KEY` from the old app to the new one. It is
impossible, and the flag now refuses with the reason rather than failing
obscurely. Measured 2026-08-22 on `prospector-hermes`:

```
$ fly ssh console -a prospector-hermes -C "... resolve_anthropic_token() ..."
env ANTHROPIC_TOKEN absent
env CLAUDE_CODE_OAUTH_TOKEN absent
env ANTHROPIC_API_KEY absent
resolve_anthropic_token -> NONE
```

Fly injects a secret into the machine's init process and its supervisord
children, not into an `fly ssh console` session. Fly also has no API that reads
a secret's value back — `fly secrets list` prints names and digests. So the only
copy of that key on the estate is inside the address space of the running
gateway. Extracting it from `/proc/<pid>/environ` is the one route left, and it
is both something an agent should not do and unnecessary.

### `--keychain` — the route that works

```
./deploy/fly/finish-cutover.sh --keychain
```

Installs this Mac's Claude Code credential onto the new app's volume at
`/root/.claude/.credentials.json`, so `resolve_anthropic_token` source #4 picks
it up and the container bills against the subscription instead of per token. It
carries a `refreshToken`, so unlike a bare `accessToken` it does not die in a few
hours.

The caveat, and it is not optional any more because this is the only route: a
refresh may rotate the token. `refresh_anthropic_oauth_pure` takes
`result.get("refresh_token", refresh_token)`, so whether it rotates is
Anthropic's choice, not ours. If it does, this Mac's own Claude Code login goes
stale and needs signing in again. Nothing is lost — it is a re-login — but it is
a surprise if nobody says it first.

The refresh writes back to `~/.claude/.credentials.json`
(`_write_claude_code_credentials`), which is why the file has to sit on the
volume. `/root/.claude` is made a symlink to `/data/dot-claude`, so a token
refreshed at 3am survives the next restart. Verified on the new container:
`HOME=/root`, `os.path.expanduser("~") -> /root`, uid 0.

### Why `claude setup-token` printed nothing

It worked. Claude Code from 2.1.114 saves the credential into the macOS login
Keychain under the service name `Claude Code-credentials` rather than printing a
code to paste. Measured 2026-08-22: that entry's `mdat` was `20260822115444Z`,
written by that run, while `~/.claude/.credentials.json` was untouched at 322
bytes dated 4 August. Upstream's own `run_oauth_setup_token()` re-reads the
credential store after the subprocess for exactly this reason.

### The macOS keychain prompt, which is what actually stopped it twice

Reading a keychain item's *attributes* is free. Reading its *value* with
`security find-generic-password -w` needs the calling binary to be in that
item's ACL, and macOS asks with a GUI prompt: "security wants to use your
confidential information stored in Claude Code-credentials". No answer to that
prompt means `-w` returns nothing.

The script used to swallow that with `|| true` and report "no Claude Code
credential in the Keychain", which is false and sends you off to re-run
`setup-token` for nothing. Measured 2026-08-22: the item was present the whole
time with `mdat=20260822115444Z` while `-w` yielded empty.

It now checks for the item first and separates the two failures. If the value
read is refused it says so and tells you to run it from a real Terminal.app
window and click **Always Allow**. An agent's shell, an editor's task runner or
any wrapper will not surface that prompt.

Every run also tees to `/tmp/finish-cutover.log`. Nothing secret is printed —
lengths and sha256 prefixes only — so that log is safe to read and safe to hand
to whoever is debugging it.

## The platform holds the secret (2026-08-22, current design)

Identity is given to the platform that runs the container. The container seeds
itself at boot. Nothing on a laptop is in the path at runtime.

```
fly secrets set CLAUDE_CODE_OAUTH_TOKEN="$(claude setup-token)" -a prospector-hermes-v2
```

The same image works anywhere the platform can set an environment variable:
ECS task-definition secrets, `gcloud run services update --set-secrets`,
`kubectl create secret`, a compose `.env`, systemd `EnvironmentFile`. Only the
command that sets the variable changes.

**A Fly secret is readable by the entrypoint and not by `fly ssh console`.**
Fly injects secrets into init and its children. `entrypoint.sh` is one; an ssh
session is not. Measured on `prospector-hermes`: an ssh shell reports
`ANTHROPIC_TOKEN absent / CLAUDE_CODE_OAUTH_TOKEN absent / ANTHROPIC_API_KEY
absent`, while `HERMES_GATEWAY_AUTOSTART`, set the same way, reaches the
entrypoint and starts the gateway. A whole afternoon was lost to reading the
first measurement as "Fly secrets do not reach this container".

**The two variables are not interchangeable.**

| variable | holds | what the resolver does |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | a setup-token string | returns it verbatim at priority 2 |
| `CLAUDE_CREDENTIALS_JSON` | the credentials document | seeded to the volume, read at priority 4, refreshed by the container |

Putting the JSON document in `CLAUDE_CODE_OAUTH_TOKEN` is the trap. Priority 2
is checked before the file at priority 4 and hands back whatever the variable
holds, so resolution succeeds, the entrypoint's check passes, and every request
sends a JSON document as its bearer token. `_prefer_refreshable_claude_code_token`
does not rescue it: a JSON blob fails `_is_oauth_token` and the helper returns
`None` (`anthropic_adapter.py:1374`, `:1459`).

A setup-token does not refresh; when it expires, set the secret again. The JSON
route refreshes itself and writes back to the volume, which is why the volume
still matters and why `/root/.claude` is symlinked to it on every boot.
