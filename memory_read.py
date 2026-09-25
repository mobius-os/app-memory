"""Deterministic paged delivery from one pinned Memory lookup manifest."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sys
from datetime import UTC, datetime

from memory_search import (
  READ_ACTIVITY_ID,
  RESULT_FAILED,
  RESULT_HIT,
  RESULT_PREFIX,
  RecallResult,
  _activity_resource,
  _catalog_page,
  _cursor_auth,
  _display,
  _result_payload,
)
from memory_store import (
  MAX_NOTE_BYTES,
  load_recall_manifest,
  read_revision_file,
  record_read_delivery,
  safe_chat_id,
)

# Bounds the complete stdout page: frames, continuation guidance, and receipt.
# It is a provider-facing delivery bound, not a count cap; as many complete
# notes as fit share a page and every remaining byte is available by cursor.
BODY_PAGE_BYTES = 12_000
_LOOKUP_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_BODY_CURSOR_RE = re.compile(
  r"^body:([0-9a-f]{16}):(\d+):(\d+):([0-9a-f]{32})$"
)
_CATALOG_CURSOR_RE = re.compile(r"^catalog:(\d+):([0-9a-f]{32})$")
_NOTE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _emit(payload: dict, text: str, *, failed: bool = False) -> int:
  print(text)
  print(RESULT_PREFIX + json.dumps(
    payload, ensure_ascii=True, separators=(",", ":"),
  ))
  return 1 if failed else 0


def _failure(lookup_id: str, reason: str) -> int:
  return _emit({
    "activity_id": READ_ACTIVITY_ID,
    "status": "failed",
    "outcome": RESULT_FAILED,
    "label": "Memory read failed",
    "warning": {
      "invalid_lookup": "The lookup reference is invalid.",
      "lookup_unavailable": "This lookup is unavailable or has expired.",
      "invalid_selection": "The requested memories are not in this catalogue.",
      "invalid_cursor": "The requested page reference is invalid.",
      "pinned_content_unavailable": "Pinned Memory content is unavailable.",
      "delivery_audit_failed": "Memory could not record this delivery safely.",
      "page_metadata_too_large": "Memory page metadata exceeded its safe budget.",
      "page_budget_exceeded": "Memory could not fit this page safely.",
    }.get(reason, "Memory could not complete this read."),
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
    or not re.fullmatch(r"[0-9a-f]{32}", str(value.get("read_id") or ""))
    or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("cursor_secret") or ""))
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
    if (
      not match
      or not hmac.compare_digest(
        match.group(2),
        _cursor_auth(manifest["cursor_secret"], "catalog", match.group(1)),
      )
    ):
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
    cursor_secret=manifest["cursor_secret"],
    discovery_complete=manifest.get("discovery_complete") is not False,
  )
  output, notes, page = _catalog_page(result, start)
  payload = _result_payload(
    result, notes=notes, page=page, activity_id=READ_ACTIVITY_ID,
  )
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


def _body_cursor(
  selection: list[dict], cursor: str, cursor_secret: str,
) -> tuple[int, int] | None:
  if cursor == "start":
    return 0, 0
  match = _BODY_CURSOR_RE.fullmatch(cursor)
  selection_hash = _selection_hash(selection)
  if not match or match.group(1) != selection_hash:
    return None
  index, offset = int(match.group(2)), int(match.group(3))
  if index < 0 or index >= len(selection) or offset < 0:
    return None
  if not hmac.compare_digest(
    match.group(4),
    _cursor_auth(cursor_secret, "body", selection_hash, index, offset),
  ):
    return None
  return index, offset


def _utf8_slice(raw: bytes, start: int, limit: int) -> tuple[str, int]:
  if start < 0 or start > len(raw):
    raise ValueError("invalid UTF-8 cursor")
  if start < len(raw) and raw[start] & 0xC0 == 0x80:
    raise ValueError("cursor is not on a UTF-8 boundary")
  end = min(len(raw), start + max(0, limit))
  while end < len(raw) and end > start and raw[end] & 0xC0 == 0x80:
    end -= 1
  return raw[start:end].decode("utf-8"), end


def _body_state(
  manifest: dict,
  selection: list[dict],
  frames: list[str],
  page_notes: list[dict],
  index: int,
  offset: int,
) -> tuple[str, dict, bytes]:
  complete = index >= len(selection)
  page = {
    "requested_count": len(selection),
    "complete": complete,
  }
  if not complete:
    selection_hash = _selection_hash(selection)
    signature = _cursor_auth(
      manifest["cursor_secret"], "body", selection_hash, index, offset,
    )
    page["next_cursor"] = f"body:{selection_hash}:{index}:{offset}:{signature}"
  output = "\n\n".join(frames)
  if not complete:
    output += (
      ("\n\n" if output else "")
      + "Memory content continues. Repeat the same selection with cursor "
      + page["next_cursor"] + "."
    )
  payload = {
    "activity_id": READ_ACTIVITY_ID,
    "status": "succeeded",
    "outcome": RESULT_HIT,
    **_display(
      RESULT_HIT,
      phase="read",
      page=page,
      note_count=len(page_notes),
    ),
    "phase": "read",
    "lookup_id": manifest["lookup_id"],
    # Pages of one selection are one read: the chat folds rows sharing this
    # key, so "Finished reading N notes" lists all N across its pages.
    "operation_key": (
      f"{manifest['lookup_id']}:read:{_selection_hash(selection)}"
    ),
    "resources": [_activity_resource(note) for note in page_notes],
    "page": page,
  }
  receipt = RESULT_PREFIX + json.dumps(
    payload, ensure_ascii=True, separators=(",", ":"),
  )
  return output, payload, (output + "\n" + receipt + "\n").encode("utf-8")


def _frame(note: dict, raw: bytes, start: int, end: int) -> str:
  metadata = {
    "id": note["id"],
    "path": note["path"],
    "sha256": hashlib.sha256(raw).hexdigest(),
    "byte_start": start,
    "byte_end": end,
    "byte_total": len(raw),
  }
  return (
    "--- MEMORY NODE START "
    + json.dumps(metadata, ensure_ascii=True, separators=(",", ":"))
    + " ---\n"
    + raw[start:end].decode("utf-8")
    + "\n--- MEMORY NODE END ---"
  )


def _body(manifest: dict, raw_selection: str, cursor: str) -> int:
  selection = _selection(manifest, raw_selection)
  if not selection:
    return _failure(str(manifest["lookup_id"]), "invalid_selection")
  position = _body_cursor(selection, cursor, manifest["cursor_secret"])
  if position is None:
    return _failure(str(manifest["lookup_id"]), "invalid_cursor")
  index, offset = position
  frames: list[str] = []
  segments: list[dict] = []
  page_notes: list[dict] = []
  commit = manifest["commit"]
  while index < len(selection):
    note = selection[index]
    try:
      content = read_revision_file(commit, note["path"], max_bytes=MAX_NOTE_BYTES)
    except (OSError, UnicodeError, ValueError):
      return _failure(str(manifest["lookup_id"]), "pinned_content_unavailable")
    raw = content.encode("utf-8")
    if offset > len(raw):
      return _failure(str(manifest["lookup_id"]), "invalid_cursor")
    note_meta = {
      "id": note["id"],
      "path": note["path"],
      "title": note.get("title") or note["id"],
    }

    def candidate(end: int):
      next_index = index + 1 if end == len(raw) else index
      next_offset = 0 if end == len(raw) else end
      next_notes = (
        page_notes if page_notes and page_notes[-1]["path"] == note["path"]
        else [*page_notes, note_meta]
      )
      next_frames = [*frames, _frame(note, raw, offset, end)]
      state = _body_state(
        manifest, selection, next_frames, next_notes, next_index, next_offset,
      )
      return state, next_frames, next_notes, next_index, next_offset

    whole = candidate(len(raw))
    if len(whole[0][2]) <= BODY_PAGE_BYTES:
      chosen = whole
    else:
      chosen = None
      low, high = 1, len(raw) - offset
      while low <= high:
        middle = (low + high) // 2
        try:
          _chunk, end = _utf8_slice(raw, offset, middle)
        except (UnicodeError, ValueError):
          return _failure(str(manifest["lookup_id"]), "invalid_cursor")
        if end == offset:
          low = middle + 1
          continue
        attempt = candidate(end)
        if len(attempt[0][2]) <= BODY_PAGE_BYTES:
          chosen = attempt
          low = middle + 1
        else:
          high = middle - 1
    if chosen is None:
      if not frames:
        return _failure(str(manifest["lookup_id"]), "page_metadata_too_large")
      break
    (_state, frames, page_notes, next_index, next_offset) = chosen
    segments.append({
      "path": note["path"], "start": offset, "end": next_offset or len(raw),
      "total": len(raw),
    })
    index, offset = next_index, next_offset
    if index < len(selection) and offset:
      break

  output, payload, rendered = _body_state(
    manifest, selection, frames, page_notes, index, offset,
  )
  if len(rendered) > BODY_PAGE_BYTES:
    return _failure(str(manifest["lookup_id"]), "page_budget_exceeded")
  print(output, flush=True)
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
  print(RESULT_PREFIX + json.dumps(
    payload, ensure_ascii=True, separators=(",", ":"),
  ), flush=True)
  return 0


def run(args: list[str]) -> int:
  if len(args) != 4:
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
  raise SystemExit(run(sys.argv[1:]))
