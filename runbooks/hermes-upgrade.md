# Runbook: upgrading Hermes

Hermes changes defaults between releases. An upgrade is a deliberate act with a
way back, never a `git pull`.

The version this estate runs is in `PINNED_VERSION`. If the running version and
that file disagree, something upgraded itself and that is the incident.

## Before

```bash
cat PINNED_VERSION
bin/hermes --version
cp state.db backups/state.db.pre-upgrade
git -C hermes-agent log --oneline -1
```

Both versions must match before you start. If they do not, stop and find out why.

## The upgrade

```bash
git -C hermes-agent fetch --tags
git -C hermes-agent log --oneline $(cat PINNED_VERSION | head -1)..<new-tag> -- \
  hermes/config hermes/constants.py hermes/agent | head -40
```

Read that list. You are looking for changed defaults, renamed config keys and
moved paths - those are what break an estate, not new features.

```bash
git -C hermes-agent checkout <new-tag>
VIRTUAL_ENV=$PWD/.venv uv pip install -e hermes-agent
```

## After - prove it, do not assume it

```bash
bin/hermes --version
bin/hermes doctor
python3 bin/check-requirements.py
```

The requirement score must not drop. A lower score after an upgrade means a
default moved under you. That is the whole reason the score exists.

Then update `PINNED_VERSION` in the same commit as the upgrade.

## Going back

```bash
git -C hermes-agent checkout $(head -1 PINNED_VERSION)
VIRTUAL_ENV=$PWD/.venv uv pip install -e hermes-agent
cp backups/state.db.pre-upgrade state.db
```

## Known breakages

_(append one line per upgrade that broke something, with the version pair)_
