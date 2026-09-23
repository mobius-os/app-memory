#!/usr/bin/env python3
"""Deterministic paged delivery from one pinned Memory lookup manifest."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import UTC, datetime

from memory_search import (
  RESULT_FAILED,
  RESULT_HIT,
  RESULT_PREFIX,
  RecallResult,
  _catalog_page,
)
from memory_store import (
  MAX_NOTE_BYTES,
  load_recall_manifest,
  read_revision_file,
  record_read_delivery,
  safe_chat_id,
)

# Includes frames but excludes the final compact receipt. This is a
# provider-facing delivery bound, not a UI-display bound: the platform parses
# Memory's receipt before compacting large output for chat history. A byte
# budget alone is sufficient, so many short notes can share a page instead of
# paying an arbitrary two-node round-trip tax.
BODY_PAGE_BYTES = 12_000
_LOOKUP_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_BODY_CURSOR_RE = re.compile(r"^body:([0-9a-f]{16}):(\d+):(\d+)$")
_CATALOG_CURSOR_RE = re.compile(r"^catalog:(\d+)$")
_NOTE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _emit(payload: dict, text: str, *, failed: bool = False) -> int:
  print(text)
  print(RESULT_PREFIX + json.dumps(
    payload, ensure_ascii=True, separators=(",", ":"),
  ))
  return 1 if failed else 0


def _failure(lookup_id: str, reason: str) -> int:
  return _emit({
    "status": RESULT_FAILED,
    "phase": "read",
    "lookup_id": lookup_id if _LOOKUP_ID_RE.fullmatch(lookup_id) else "0" * 64,
    "reason": reason,
  }, "Memory read failed.", failed=True)


def _parse_time(value: object) -> datetime | None:
  if not isinstance(value, str):
    return None
  try:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
  except ValueError:
    return None
  if parsed.tzinfo is None:
    return None
  return parsed.astimezone(UTC)


def _manifest(lookup_id: str, chat_id: str) -> dict | None:
  value = load_recall_manifest(lookup_id)
  if (
    not value
    or value.get("lookup_id") != lookup_id
    or value.get("chat_id") != safe_chat_id(chat_id)
    or value.get("status") not in {"hit", "empty"}
    or not isinstance(value.get("commit"), str)
    or not isinstance(value.get("read_id"), str)
  ):
    return None
  expires = _parse_time(value.get("expires_at"))
  if expires is None or datetime.now(UTC) > expires:
    return None
  candidates = value.get("candidates")
  if not isinstance(candidates, list):
    return None
  seen_ids: set[str] = set()
  seen_paths: set[str] = set()
  for item in candidates:
    if not isinstance(item, dict):
      return None
    node_id = item.get("id")
    path = item.get("path")
    if (
      not isinstance(node_id, str)
      or not _NOTE_ID_RE.fullmatch(node_id)
      or not isinstance(path, str)
      or node_id in seen_ids
      or path in seen_paths
    ):
      return None
    seen_ids.add(node_id)
    seen_paths.add(path)
  return value


def _catalog(manifest: dict, cursor: str) -> int:
  if cursor == "start":
    start = 0
  else:
    match = _CATALOG_CURSOR_RE.fullmatch(cursor)
    if not match:
      return _failure(str(manifest["lookup_id"]), "invalid_cursor")
    start = int(match.group(1))
  candidates = manifest["candidates"]
  if start < 0 or start >= len(candidates):
    return _failure(str(manifest["lookup_id"]), "invalid_cursor")
  result = RecallResult(
    RESULT_HIT,
    "Relevant Memory catalogue:",
    files=tuple(item["path"] for item in candidates),
    commit=manifest["commit"],
    notes=tuple(candidates),
    lookup_id=manifest["lookup_id"],
    read_id=manifest["read_id"],
    discovery_complete=manifest.get("discovery_complete") is not False,
  )
  output, notes, page = _catalog_page(result, start)
  payload = {
    "status": RESULT_HIT,
    "phase": "catalog",
    "lookup_id": manifest["lookup_id"],
    "notes": notes,
    "page": page,
    "discovery_complete": result.discovery_complete,
  }
  return _emit(payload, output)


def _selection(manifest: dict, raw: str) -> list[dict] | None:
  candidates = manifest["candidates"]
  if raw == "all":
    return list(candidates)
  try:
    ids = json.loads(raw)
  except json.JSONDecodeError:
    return None
  if (
    not isinstance(ids, list)
    or not ids
    or any(not isinstance(node_id, str) for node_id in ids)
    or len(ids) != len(set(ids))
  ):
    return None
  by_id = {item["id"]: item for item in candidates}
  if any(node_id not in by_id for node_id in ids):
    return None
  wanted = set(ids)
  return [item for item in candidates if item["id"] in wanted]


def _selection_hash(selection: list[dict]) -> str:
  raw = json.dumps(
    [item["id"] for item in selection],
    ensure_ascii=True,
    separators=(",", ":"),
  ).encode("ascii")
  return hashlib.sha256(raw).hexdigest()[:16]


def _body_cursor(selection: list[dict], cursor: str) -> tuple[int, int] | None:
  if cursor == "start":
    return 0, 0
  match = _BODY_CURSOR_RE.fullmatch(cursor)
  if not match or match.group(1) != _selection_hash(selection):
    return None
  index, offset = int(match.group(2)), int(match.group(3))
  if index < 0 or index >= len(selection) or offset < 0:
    return None
  return index, offset


def _utf8_slice(raw: bytes, start: int, limit: int) -> tuple[str, int]:
  end = min(len(raw), start + max(1, limit))
  while end > start:
    try:
      return raw[start:end].decode("utf-8"), end
    except UnicodeDecodeError as exc:
      if exc.start == 0:
        raise ValueError("cursor is not on a UTF-8 boundary") from exc
      end = start + exc.start
  raise ValueError("unable to make UTF-8 progress")


def _body(manifest: dict, raw_selection: str, cursor: str) -> int:
  selection = _selection(manifest, raw_selection)
  if not selection:
    return _failure(str(manifest["lookup_id"]), "invalid_selection")
  position = _body_cursor(selection, cursor)
  if position is None:
    return _failure(str(manifest["lookup_id"]), "invalid_cursor")
  index, offset = position
  remaining = BODY_PAGE_BYTES
  frames: list[str] = []
  segments: list[dict] = []
  page_notes: list[dict] = []
  commit = manifest["commit"]
  while (
    index < len(selection)
    and remaining > 512
  ):
    note = selection[index]
    try:
      content = read_revision_file(commit, note["path"], max_bytes=MAX_NOTE_BYTES)
    except (OSError, UnicodeError, ValueError):
      return _failure(str(manifest["lookup_id"]), "pinned_content_unavailable")
    raw = content.encode("utf-8")
    if offset > len(raw):
      return _failure(str(manifest["lookup_id"]), "invalid_cursor")
    digest = hashlib.sha256(raw).hexdigest()
    header_data = {
      "id": note["id"], "path": note["path"], "sha256": digest,
      "byte_start": offset, "byte_total": len(raw),
    }
    provisional = json.dumps(header_data, ensure_ascii=True, separators=(",", ":"))
    overhead = len((f"--- MEMORY NODE START {provisional} ---\n\n"
                    "--- MEMORY NODE END ---\n").encode("utf-8")) + 32
    room = max(1, remaining - overhead)
    if len(raw) == 0:
      chunk, end = "", 0
    else:
      try:
        chunk, end = _utf8_slice(raw, offset, room)
      except ValueError:
        return _failure(str(manifest["lookup_id"]), "invalid_cursor")
    header_data["byte_end"] = end
    header = json.dumps(header_data, ensure_ascii=True, separators=(",", ":"))
    frame = (
      f"--- MEMORY NODE START {header} ---\n"
      + chunk
      + "\n--- MEMORY NODE END ---"
    )
    frame_size = len((frame + "\n").encode("utf-8"))
    frames.append(frame)
    remaining -= frame_size
    segments.append({
      "path": note["path"], "start": offset, "end": end,
      "total": len(raw),
    })
    if not page_notes or page_notes[-1]["path"] != note["path"]:
      page_notes.append({
        "id": note["id"], "path": note["path"], "title": note.get("title") or note["id"],
      })
    if end < len(raw):
      offset = end
      break
    index += 1
    offset = 0
  complete = index >= len(selection)
  page = {
    "requested_count": len(selection),
    "fully_supplied_count": index,
    "complete": complete,
  }
  if not complete:
    page["next_cursor"] = f"body:{_selection_hash(selection)}:{index}:{offset}"
  try:
    record_read_delivery(
      read_id=manifest["read_id"],
      lookup_id=manifest["lookup_id"],
      commit=commit,
      candidates=manifest["candidates"],
      requested_paths=[item["path"] for item in selection],
      segments=segments,
    )
  except (OSError, ValueError):
    return _failure(str(manifest["lookup_id"]), "delivery_audit_failed")
  output = "\n\n".join(frames)
  if not complete:
    output += (
      "\n\nMemory content continues. Repeat the same selection with cursor "
      + page["next_cursor"] + "."
    )
  return _emit({
    "status": RESULT_HIT,
    "phase": "read",
    "lookup_id": manifest["lookup_id"],
    "notes": page_notes,
    "page": page,
  }, output)


def run() -> int:
  args = sys.argv[1:]
  if len(args) != 4:
    sys.stderr.write(
      'usage: memory_read.py "<lookup_id>" "<catalog|all|JSON ids>" '
      '"<cursor>" "<chat_id>"\n'
    )
    return 2
  lookup_id, selection, cursor, chat_id = args
  if not _LOOKUP_ID_RE.fullmatch(lookup_id):
    return _failure(lookup_id, "invalid_lookup")
  manifest = _manifest(lookup_id, chat_id)
  if manifest is None:
    return _failure(lookup_id, "lookup_unavailable")
  if selection == "catalog":
    return _catalog(manifest, cursor)
  return _body(manifest, selection, cursor)


if __name__ == "__main__":
  raise SystemExit(run())
