# Graph Report - hermes-v2  (2026-08-22)

## Corpus Check
- 39 files · ~105,646 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 141 nodes · 132 edges · 32 communities (19 shown, 13 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `e2a63887`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]

## God Nodes (most connected - your core abstractions)
1. `HandlerError` - 8 edges
2. `handle()` - 7 edges
3. `estate-map` - 7 edges
4. `post-mortem` - 7 edges
5. `pr-discipline` - 7 edges
6. `create_issue()` - 6 edges
7. `raises()` - 6 edges
8. `Runbook: upgrading Hermes` - 6 edges
9. `incident-triage` - 6 edges
10. `screenshot-to-story` - 6 edges

## Surprising Connections (you probably didn't know these)
- None detected - all connections are within the same source files.

## Import Cycles
- None detected.

## Communities (32 total, 13 thin omitted)

### Community 2 - "Community 2"
Cohesion: 0.25
Nodes (7): 1. The timeline, from commands, 2. The class, 3. Name the rung, 4. Close it mechanically, in this order, 5. Write it into the eval set, 6. Post it, post-mortem

### Community 3 - "Community 3"
Cohesion: 0.25
Nodes (7): 0. Pre-flight - two questions before the first command, 1. A worktree, never the main checkout, 2. Reproduce before you fix, 3. Smallest diff, 4. Green before you push, 5. The PR, pr-discipline

### Community 4 - "Community 4"
Cohesion: 0.25
Nodes (7): estate-map, Is it serving, Report shape, The apps, The board, The repos, Thresholds

### Community 5 - "Community 5"
Cohesion: 0.29
Nodes (6): 1. Confirm it twice, from two angles, 2. Get the reason, not the symptom, 3. What changed, 4. Open exactly one issue, incident-triage, Never

### Community 6 - "Community 6"
Cohesion: 0.29
Nodes (6): 1. Read the image, do not guess at it, 2. Locate it, 3. Ask the one question that matters, in the issue, 4. File it, Never, screenshot-to-story

### Community 7 - "Community 7"
Cohesion: 0.33
Nodes (5): 1. Merged, 2. Deployed - the pipeline ran and the app took the release, 3. Working - the behaviour the issue asked for, 4. Two angles, or it is not verified, verify-to-prod

### Community 8 - "Community 8"
Cohesion: 0.33
Nodes (5): How to review one, Not installed - could not be found, Reviewed and installed, Reviewed and rejected, Vetted third-party skills

### Community 10 - "Community 10"
Cohesion: 0.22
Nodes (16): Exception, build_body(), clean_caption(), create_issue(), _default_post(), handle(), HandlerError, main() (+8 more)

### Community 11 - "Community 11"
Cohesion: 0.19
Nodes (8): dry_run_files_nothing(), expired_token(), gives_up(), no_retry_on_422(), no_token(), raises(), short_token(), tmp()

### Community 12 - "Community 12"
Cohesion: 0.38
Nodes (5): CLAIM_WORDS, fencedBlocks(), findClaims(), main(), PLACEHOLDERS

### Community 13 - "Community 13"
Cohesion: 0.29
Nodes (6): After - prove it, do not assume it, Before, Going back, Known breakages, Runbook: upgrading Hermes, The upgrade

### Community 14 - "Community 14"
Cohesion: 0.33
Nodes (5): Blocked on a founder decision, not on work, Cutover, Going back, Order of cutover, The Telegram token is shared

### Community 15 - "Community 15"
Cohesion: 0.50
Nodes (3): Rituals, Sunday review - 20 minutes, The lesson ladder

## Knowledge Gaps
- **61 isolated node(s):** `Blocked on a founder decision, not on work`, `The Telegram token is shared`, `Order of cutover`, `Going back`, `Platform map` (+56 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **13 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What connects `Blocked on a founder decision, not on work`, `The Telegram token is shared`, `Order of cutover` to the rest of the system?**
  _65 weakly-connected nodes found - possible documentation gaps or missing edges._