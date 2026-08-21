# Cutover

The new estate does not replace the old one by being finished. It replaces it by
a sequence of steps, each of which can be undone.

## Blocked on a founder decision, not on work

Two settings on `chidionyema/prospector` are in the spec and are **not** applied,
because applying them today would stop the live pipeline. Measured 2026-08-22:

**1. Auto-merge is on, and two PRs are queued on it.**

```
$ gh api repos/chidionyema/prospector --jq .allow_auto_merge
true
$ gh pr list -R chidionyema/prospector --state open --json number,autoMergeRequest
#643 auto_merge=true  ci: delete the workarounds the public repo made unnecessary
#627 auto_merge=true  fix(backup): one backup system that can restore the ledger, not two that cannot
```

The repo also runs `automerge.yml` and `merge-when-green.yml`. Spec §10 says the
founder tap is the only promotion. Turning auto-merge off cancels both queued
PRs and takes two active workflows out of service.

The command, when he decides:

```bash
gh api -X PATCH repos/chidionyema/prospector -f allow_auto_merge=false
```

**2. main has no branch protection.**

```
$ gh api repos/chidionyema/prospector/branches/main/protection
Branch not protected (HTTP 404)
```

Requiring `evidence-gate` and `static-gates` before those workflows exist in
prospector freezes main permanently - a required check that never reports is a
merge that never happens.

Order that works: copy `ci/evidence-gate.yml` and `ci/static-gates.yml` into
`prospector/.github/workflows/`, open a PR, watch both run green once, **then**:

```bash
gh api -X PUT repos/chidionyema/prospector/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": {"strict": true, "contexts": ["evidence-gate", "static-gates"]},
  "enforce_admins": false,
  "required_pull_request_reviews": {"required_approving_review_count": 1},
  "restrictions": null
}
JSON
```

`enforce_admins: false` on purpose. The founder must be able to merge past a
stuck gate at 2am without an argument with GitHub.

## The Telegram token is shared

The new estate uses the same bot token as the old one. Two gateways on one token
fight over updates and messages go missing. So:

- the old gateway stays off, permanently (REQ-116);
- before starting the new gateway, prove nothing else is polling:

```bash
pgrep -fl 'hermes.*gateway' || echo "nothing polling - safe to start"
```

## Order of cutover

1. New estate runs WATCH only, old estate off. One week. Nothing writes.
2. Add WORK on one issue, by hand, and read the PR it opens.
3. Install the two CI workflows in prospector, watch them go green.
4. Apply branch protection and turn auto-merge off (the two commands above).
5. Point Telegram at the new gateway.
6. Old estate stays on disk, frozen, for a month before anything is deleted.

## Going back

At any step: stop the new gateway, start the old one, done. The old estate is
untouched at `~/.hermes` on `main` and nothing in this build writes to it.
