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

### `--copy-api-key` — the one to run

```
./deploy/fly/finish-cutover.sh --copy-api-key
```

Copies `ANTHROPIC_API_KEY` from `prospector-hermes` to `prospector-hermes-v2`,
then flips the gateway across and rolls back on its own if it does not connect.
The value goes from one `fly` process's stdout into another's stdin. It is never
echoed, never written to a file, never in argv, never in shell history. What is
printed is a character count and 12 hex of its sha256, which compares two copies
and is not one.

This is the key the old gateway already bills against, so the cutover changes
nothing about what is spent. Nothing to paste, no browser, no prompt.

### `--keychain` — cheaper, with a caveat that is his call

```
./deploy/fly/finish-cutover.sh --keychain
```

Installs this Mac's Claude Code credential onto the new app's volume at
`/root/.claude/.credentials.json`, so `resolve_anthropic_token` source #4 picks
it up and the container bills against the subscription instead of per token. It
carries a `refreshToken`, so unlike a bare `accessToken` it does not die in a few
hours.

The caveat: a refresh may rotate the token. If it does, this Mac's own Claude
Code login can go stale and need signing in again. Nothing is lost — it is a
re-login — but it is a surprise if nobody said it first.

### Why `claude setup-token` printed nothing

It worked. Claude Code from 2.1.114 saves the credential into the macOS login
Keychain under the service name `Claude Code-credentials` rather than printing a
code to paste. Measured 2026-08-22: that entry's `mdat` was `20260822115444Z`,
written by that run, while `~/.claude/.credentials.json` was untouched at 322
bytes dated 4 August. Upstream's own `run_oauth_setup_token()` re-reads the
credential store after the subprocess for exactly this reason.
