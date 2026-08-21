# Graph Report - hermes-v2  (2026-08-22)

## Corpus Check
- 30 files · ~8,338 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 52 nodes · 42 edges · 10 communities (9 shown, 1 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `897f1d94`
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

## God Nodes (most connected - your core abstractions)
1. `post-mortem` - 7 edges
2. `pr-discipline` - 7 edges
3. `estate-map` - 6 edges
4. `incident-triage` - 6 edges
5. `screenshot-to-story` - 6 edges
6. `verify-to-prod` - 5 edges
7. `Vetted third-party skills` - 4 edges
8. `Platform gating` - 1 edges
9. `Reviewed and installed` - 1 edges
10. `Reviewed and rejected` - 1 edges

## Surprising Connections (you probably didn't know these)
- None detected - all connections are within the same source files.

## Import Cycles
- None detected.

## Communities (10 total, 1 thin omitted)

### Community 2 - "Community 2"
Cohesion: 0.25
Nodes (7): 1. The timeline, from commands, 2. The class, 3. Name the rung, 4. Close it mechanically, in this order, 5. Write it into the eval set, 6. Post it, post-mortem

### Community 3 - "Community 3"
Cohesion: 0.25
Nodes (7): 0. Pre-flight - two questions before the first command, 1. A worktree, never the main checkout, 2. Reproduce before you fix, 3. Smallest diff, 4. Green before you push, 5. The PR, pr-discipline

### Community 4 - "Community 4"
Cohesion: 0.29
Nodes (6): estate-map, Is it serving, Report shape, The apps, The board, The repos

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
Cohesion: 0.40
Nodes (4): How to review one, Reviewed and installed, Reviewed and rejected, Vetted third-party skills

## Knowledge Gaps
- **35 isolated node(s):** `Platform gating`, `Reviewed and installed`, `Reviewed and rejected`, `How to review one`, `The apps` (+30 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What connects `Platform gating`, `Reviewed and installed`, `Reviewed and rejected` to the rest of the system?**
  _35 weakly-connected nodes found - possible documentation gaps or missing edges._