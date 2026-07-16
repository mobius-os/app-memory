---
title: Maintaining memory
type: moc
tags: [meta]
managed_by: memory
managed_schema: 1
---
# Maintaining memory

How the installed Memory app grows this knowledge graph. The app's scheduled,
filesystem-confined consolidator owns writes. A chat agent requests focused,
read-only recall through Memory's system-prompt contribution and uses only the
returned text and verified file pointers.

## The system

- [[how-the-memory-graph-works]] — the separation between platform chat
  summaries, the optional graph, scheduled consolidation, and focused recall.
- [[memory-is-visible-to-the-partner]] — when installed, the Memory app shows
  every note to the partner; write as if you'd stand behind it when quoted back.

## The short version

- **Promote sparingly.** The scheduled pass records durable, future-useful facts
  *about the user and this instance* (preferences, interests, personality, or a
  hard-won local bug + root cause), with chat provenance. It defaults to no
  graph change.
- **Keep continuity separate.** The base platform owns each chat's bounded
  Digest and cumulative Summary. They remain available when Memory is removed
  and are never graph startup context.
- **One idea per note.** Title it as the specific claim. Link every note into
  at least one map ([[index]] → maps → notes). No orphans.
- **Recall deliberately.** The main agent formulates what prior context it
  needs; Memory's tool-free reader selects paths from one pinned immutable
  commit, and the host verifies them before opening files.
