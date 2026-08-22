# The Mac side: identity

The container owns the machine. This Mac owns the human identity. Neither
crosses into the other, and no agent sits between them — four permission
refusals in one session established that an agent acting as courier between a
keychain and a container is the thing that keeps failing, not the auth mechanism
underneath it.

## What is here

| file | what it does |
|---|---|
| `hermes-auth-bridge` | reads the Claude credential from this Mac's login keychain and writes it to the Fly volume when it changes |
| `ai.hermes.auth-bridge.plist` | runs the bridge at load and every four hours |

`~/.local/bin/hermes-auth-bridge` is a symlink to the copy here, so a fix
committed to this repo is live with no second install, and the bridge leaves
with the code in a git bundle.

## Install

```
ln -sfn "$PWD/deploy/mac/hermes-auth-bridge" ~/.local/bin/hermes-auth-bridge
cp deploy/mac/ai.hermes.auth-bridge.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/ai.hermes.auth-bridge.plist
```

## The one human step, once

Reading a keychain item's **attributes** is free. Reading its **value** needs
the calling binary in that item's ACL, and macOS asks with a dialog the first
time. Click **Always Allow** and `/usr/bin/security` is added to the ACL and is
never asked again — including from launchd, which has no GUI session to prompt
in and would otherwise fail silently forever.

So the first run must be interactive, in Terminal.app:

```
~/.local/bin/hermes-auth-bridge --force
```

After that click, every later run is unattended.

## What it prints

Hashes, lengths, expiry timestamps and exit codes. Never a token. The log at
`~/.local/state/hermes-auth-bridge/sync.log` is safe to read and safe to hand to
whoever is debugging it.

```
hermes-auth-bridge            sync if the credential changed
hermes-auth-bridge --force    sync even if it looks unchanged
hermes-auth-bridge --status   say what it knows, change nothing
```
