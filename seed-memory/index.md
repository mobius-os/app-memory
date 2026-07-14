---
title: Memory — Home
type: moc
---
# Memory

This is your **Home** map — the root of your knowledge graph at
`/data/shared/memory/`, surfaced as the **Memory** app when that app is
installed. It is never injected at session start. The live Memory app's system
prompt teaches the main agent to formulate a focused question for its read-only
lookup subagent, which traverses this map and returns only relevant facts with
file pointers. Everything below is reachable from here.

The scheduled Memory consolidator records what is **useful for the future and
specific to this user/instance** — durable facts about the partner
(preferences, interests, personality), and hard-won bugs hit *here* — not
everything, and not generic app/platform how-to. The main chat agent is a
read-only consumer of this graph; it does not maintain these files during a
turn. See [[how-the-memory-graph-works]] for the boundary.

This graph starts almost empty by design — a scaffold of maps with no facts yet.
It **grows through use**. The **Memory** app owns scheduled consolidation; the
graph has no dependency on any other app.

## Maps

- [[about-the-user]] — who the user is: preferences, interests, personality,
  how they want you to work. *The primary map — start here when a chat hints at
  a durable preference, and grow it first.*
- [[building-mobius-apps]] — app facts specific to this user/instance (general
  app-building technique lives in skills, not here).
- [[mobius-platform]] — operational facts specific to this deployment (general
  platform how-to lives in skills, not here).
- [[maintaining-memory]] — how the installed Memory app grows this graph.

## Notes

- [[memory-is-visible-to-the-partner]] — when installed, the Memory app shows
  every note to the partner; write as if you'd stand behind it when quoted back.

## Recent chats

Each chat keeps its own platform-owned note (`chats/<id>/index.md`) with a
one-line name, bounded Digest, and cumulative full Summary. The base platform
injects only recent names + Digests. Those chat notes are continuity state, not
knowledge-graph nodes; the scheduled Memory app receives structurally redacted
chat text through its reviewed capability and promotes only durable facts.
