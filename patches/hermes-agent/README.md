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
- `0003-feat-claim_gate-...` a DONE the verification ledger cannot back is
  restamped UNVERIFIED
- `0004-fix-gateway-clarify-...` the gateway consumed a clarify answer in
  memory, acknowledged with an empty string and never wrote the words down.
  The founder typed 46 characters at 02:03:02 on 2026-08-24 and they were
  unrecoverable from every store on this machine
- `0005-fix-gateway-steer-...` the same swallow on the `/steer` path and the
  two busy-follow-up paths that reach `steer()`

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
