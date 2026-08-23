# Our patches to the vendored hermes-agent

`hermes-agent/` is a checkout of `NousResearch/hermes-agent`, 977 MB, and the
parent `.gitignore` skips it as "reinstallable from PINNED_VERSION". That was
true until we patched it. A local commit in a repo whose only remote belongs to
someone else is one `rm -rf` from gone, and nothing here would show the diff.

These files are that diff. `BASE` names the upstream commit they apply on top of.

## What is in here

- `0001-feat-summary-...` the isopsephy card ported from the old estate
- `0002-fix-shutdown_forensics-...` the shutdown diagnostic ran Linux-only
  commands on macOS and wrote four complete-looking reports with every section
  empty

## Reapplying after a reinstall

```
cd hermes-agent
git checkout $(cat ../patches/hermes-agent/BASE)
git am ../patches/hermes-agent/*.patch
```

## Refreshing these files after a new local commit

```
cd hermes-agent
git format-patch --no-signature -o ../patches/hermes-agent $(cat ../patches/hermes-agent/BASE)..HEAD
```
