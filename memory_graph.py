"""App-owned deterministic builder for one Memory graph commit."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path


_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
_SOURCE_ID = re.compile(r"^[0-9a-f]{32}$")

# Structural-quality thresholds. These drive *warnings*, not errors: they flag
# split candidates so run-status/update-log can resurface them, but they never
# block publication of an otherwise-valid commit (see run() in memory_runner).
MAX_NOTE_PROSE_LINES = 30  # one atomic claim per note; longer prose = split it
MAX_MAP_ENTRIES = 30  # a map this wide is a navigation hazard; split into submaps


def _prose_line_count(text: str) -> int:
  """Non-blank body lines, excluding the YAML frontmatter block.

  The oversized-note heuristic measures prose length, not byte size, so a note
  with long wrapped lines is not penalised while a note that has accreted many
  separate claims is. Blank lines and frontmatter do not count.
  """
  body = text
  if text.startswith("---\n"):
    end = text.find("\n---", 4)
    if end >= 0:
      newline = text.find("\n", end + 1)
      body = text[newline + 1:] if newline >= 0 else ""
  return sum(1 for line in body.splitlines() if line.strip())


def _frontmatter(text: str) -> dict:
  if not text.startswith("---\n"):
    return {}
  end = text.find("\n---", 4)
  if end < 0:
    return {}
  result = {}
  for line in text[4:end].splitlines():
    if ":" not in line:
      continue
    key, raw = line.split(":", 1)
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
      result[key.strip()] = [
        item.strip().strip("'\"") for item in raw[1:-1].split(",")
        if item.strip()
      ]
    elif raw.lstrip("-").isdigit():
      result[key.strip()] = int(raw)
    else:
      result[key.strip()] = raw.strip("'\"")
  return result


def _slug_for(path: Path, root: Path) -> str:
  rel = path.relative_to(root).as_posix()
  if rel == "index.md":
    return "index"
  return path.stem


def _source_catalog(root: Path) -> tuple[dict[str, dict], dict[str, dict]]:
  """Index compact supporting-chat metadata for note provenance."""
  by_id: dict[str, dict] = {}
  by_chat_id: dict[str, dict] = {}
  directory = root / "sources"
  if directory.is_symlink() or not directory.is_dir():
    return by_id, by_chat_id
  for path in sorted(directory.glob("*.json")):
    source_id = path.stem
    if (
      not _SOURCE_ID.fullmatch(source_id)
      or path.is_symlink()
      or not path.is_file()
    ):
      continue
    try:
      value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
      continue
    if not isinstance(value, dict) or value.get("source_id") != source_id:
      continue
    active_chat_id = value.get("chat_id")
    deleted = bool(
      value.get("deleted_at")
      or value.get("source_unavailable_at")
      or not active_chat_id
    )
    ref = {
      "source_id": source_id,
      "kind": "deleted" if deleted else "active",
      "last_activity": str(value.get("last_activity") or "")[:80],
    }
    if not deleted:
      ref["chat_id"] = str(active_chat_id)[:128]
      ref["title"] = str(value.get("title") or "")[:300]
    by_id[source_id] = ref
    chat_id = active_chat_id
    if isinstance(chat_id, str) and chat_id:
      by_chat_id[chat_id] = ref
  return by_id, by_chat_id


def _source_refs(tokens: object, by_id: dict[str, dict], by_chat_id: dict[str, dict]) -> list[dict]:
  if not isinstance(tokens, list):
    return []
  refs = []
  seen = set()
  for raw in tokens:
    token = str(raw or "").strip()
    if not token or token in seen:
      continue
    seen.add(token)
    if token.startswith("chat:"):
      chat_id = token[5:]
      ref = by_chat_id.get(chat_id)
      refs.append(dict(ref) if ref else {
        "kind": "active",
        "chat_id": chat_id,
        "title": "",
        "last_activity": "",
      })
      continue
    if token == "deleted-chat":
      refs.append({
        "kind": "legacy_deleted",
        "last_activity": "",
      })
      continue
    if token.startswith("deleted-chat:"):
      source_id = token[len("deleted-chat:"):]
      ref = by_id.get(source_id)
      refs.append({**ref, "kind": "deleted"} if ref else {
        "kind": "deleted",
        "last_activity": "",
      })
  return refs


def build(root: Path, *, usage: dict[str, int] | None = None) -> dict:
  usage = usage or {}
  sources_by_id, sources_by_chat_id = _source_catalog(root)
  files = [root / "index.md"]
  files.extend(sorted((root / "mocs").glob("*.md")))
  files.extend(sorted((root / "notes").glob("*.md")))
  nodes = []
  links_by_source: dict[str, list[str]] = {}
  paths_by_id = {}
  problems = []
  for path in files:
    if not path.is_file() or path.is_symlink():
      continue
    text = path.read_text(encoding="utf-8")
    fm = _frontmatter(text)
    node_id = _slug_for(path, root)
    rel = path.relative_to(root).as_posix()
    previous_path = paths_by_id.get(node_id)
    if previous_path is not None:
      problems.append({
        "kind": "duplicate_id",
        "severity": "error",
        "node": node_id,
        "paths": [previous_path, rel],
      })
    node_type = str(fm.get("type") or ("moc" if rel.startswith("mocs/") else "note"))
    if node_id == "index":
      node_type = "moc"
    title = str(fm.get("title") or node_id.replace("-", " ").title())
    description = str(fm.get("description") or "")
    mocs = fm.get("mocs") if isinstance(fm.get("mocs"), list) else []
    importance = fm.get("importance") if isinstance(fm.get("importance"), int) else 1
    source_refs = _source_refs(
      fm.get("source"), sources_by_id, sources_by_chat_id,
    )
    node = {
      "id": node_id,
      "title": title,
      "description": description,
      "type": node_type,
      "path": rel,
      "mocs": mocs,
      "tags": fm.get("tags") if isinstance(fm.get("tags"), list) else [],
      "source_refs": source_refs,
      "importance": max(1, importance),
      "access_count": int(usage.get(node_id, 0)),
      "updated": str(fm.get("updated") or fm.get("as-of") or ""),
      "bytes": len(text.encode("utf-8")),
    }
    managed_by = fm.get("managed_by")
    if isinstance(managed_by, str) and managed_by:
      node["managed_by"] = managed_by[:80]
    nodes.append(node)
    paths_by_id.setdefault(node_id, rel)
    wikilinks = [match.strip() for match in _WIKILINK.findall(text)]
    links_by_source[node_id] = wikilinks
    if node_type == "moc":
      entries = {Path(target).stem for target in wikilinks if target}
      if len(entries) > MAX_MAP_ENTRIES:
        problems.append({
          "kind": "overfull_map",
          "severity": "warning",
          "node": node_id,
          "entries": len(entries),
        })
    else:
      if source_refs and not description.strip():
        problems.append({
          "kind": "missing_description",
          "severity": "warning",
          "node": node_id,
        })
      prose_lines = _prose_line_count(text)
      if prose_lines > MAX_NOTE_PROSE_LINES:
        problems.append({
          "kind": "oversized_note",
          "severity": "warning",
          "node": node_id,
          "lines": prose_lines,
        })

  ids = set(paths_by_id)
  edges = []
  seen = set()
  for source, targets in links_by_source.items():
    for raw_target in targets:
      target = Path(raw_target).stem
      if target not in ids:
        problems.append({
          "kind": "dangling_link", "severity": "error",
          "source": source, "target": raw_target,
        })
        continue
      key = (source, target)
      if source != target and key not in seen:
        seen.add(key)
        edges.append({"source": source, "target": target, "kind": "link"})
  adjacency: dict[str, list[str]] = {}
  for edge in edges:
    adjacency.setdefault(edge["source"], []).append(edge["target"])
  reachable = set()
  pending = ["index"] if "index" in ids else []
  while pending:
    node_id = pending.pop()
    if node_id in reachable:
      continue
    reachable.add(node_id)
    pending.extend(adjacency.get(node_id, ()))
  for node in nodes:
    if node["id"] != "index" and node["id"] not in reachable:
      problems.append({"kind": "orphan", "severity": "error", "node": node["id"]})
  moc_ids = {node["id"] for node in nodes if node["type"] == "moc"}
  for node in nodes:
    if node["type"] == "moc":
      continue
    for moc in node["mocs"]:
      target = Path(str(moc)).stem
      if target and target not in moc_ids:
        problems.append({
          "kind": "bare_map_entry",
          "severity": "warning",
          "node": node["id"],
          "moc": str(moc),
        })
  result = {
    "schema": 1,
    "generated_at": datetime.now(UTC).isoformat(),
    "nodes": nodes,
    "edges": edges,
    "problems": problems,
  }
  (root / "graph.json").write_text(
    json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    + "\n",
    encoding="utf-8",
  )
  return result
