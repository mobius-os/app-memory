# Memory

An Obsidian-style view of what [Möbius](https://github.com/mobius-os) knows about you. Memory renders the agent's knowledge graph as a force-directed view: each node is a small markdown note or a map-of-content, and the links between them are the connections the agent drew as it learned. Tap any node to read the note behind it.

The graph starts from Memory's small seed map and fills in over time — Möbius writes a note when it learns something durable about you, your apps, or how you like to work, and links it into the web. Memory is both the window onto that knowledge and the app that owns scheduled graph maintenance.

## Install

### Via the App Store (recommended)

Open the **App Store** mini-app in Möbius, find **Memory**, tap **Install**.

### Via paste-a-URL

In the App Store, choose **Install from URL** and paste:

```
https://raw.githubusercontent.com/mobius-os/app-memory/main/mobius.json
```

Möbius will fetch the manifest, show you the requested permissions and schedule, and install with one tap.

## What you'll see

- **The graph** — notes and maps-of-content as nodes, sized by how often they're touched; `moc` links (a note belongs to a map) and `link` links (one note references another) as edges.
- **Node detail** — tap a node to read its markdown (frontmatter + body), rendered safely.
- **Health hints** — dangling links, orphans, and other graph problems surface so the agent (and you) can see where the graph needs tidying.

Memory reads `GET /api/storage/shared/memory/graph.json` and the individual note files under `shared/memory/`. The visible app is a read-only viewer; its scheduled job defaults to 05:30 and can be rescheduled from Memory's Maintenance settings. The job uses Möbius's Background agents order, initializes the graph when needed, consolidates chat notes, rebuilds `graph.json`, and appends a compact maintenance record under `shared/memory/update-log/`.

Installing Memory also contributes a small system-prompt fragment and graph skill. The fragment activates an app-local, prompt-scoped reader: the chat agent states what prior context it needs, a read-only background agent traverses the graph, and the result comes back with verified markdown file pointers. The graph is never injected wholesale. Uninstalling Memory removes that prompt contribution after the next server restart while preserving the owner's graph data for recovery/reinstall. The deterministic graph indexer remains a platform utility used by the app runner.

That split keeps the responsibility clear: Memory reads, writes, and consolidates memory. Reflection may review Memory's update log when it prepares a morning brief, but it does not own the graph.

## License

MIT
