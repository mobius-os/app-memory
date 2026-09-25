import contextlib
import hashlib
import importlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
FRAME_RE = re.compile(
  r"--- MEMORY NODE START (?P<meta>\{[^\n]+\}) ---\n"
  r"(?P<body>.*?)\n--- MEMORY NODE END ---",
  re.DOTALL,
)


def _load(data_dir: Path):
  for name in ("memory_read", "memory_search", "memory_store"):
    sys.modules.pop(name, None)
  sys.path.insert(0, str(REPO))
  try:
    with mock.patch.dict(os.environ, {"DATA_DIR": str(data_dir)}):
      store = importlib.import_module("memory_store")
      search = importlib.import_module("memory_search")
      reader = importlib.import_module("memory_read")
  finally:
    sys.path.remove(str(REPO))
  return store, search, reader


def _publish(store, bodies: list[str], node_ids: list[str] | None = None):
  node_ids = node_ids or [f"note-{index:02d}" for index in range(len(bodies))]
  seed = store.ROOT / "seed"
  (seed / "mocs").mkdir(parents=True, exist_ok=True)
  (seed / "notes").mkdir(exist_ok=True)
  links = "\n".join(f"- [[{node_id}]]" for node_id in node_ids)
  (seed / "index.md").write_text(f"# Memory\n\n{links}\n", encoding="utf-8")
  _, worktree = store.start_staging(seed)
  nodes = [{
    "id": "index", "type": "index", "title": "Memory",
    "description": "Root", "path": "index.md",
  }]
  edges = []
  for index, body in enumerate(bodies):
    node_id = node_ids[index]
    path = f"notes/{node_id}.md"
    (worktree / path).write_text(body, encoding="utf-8")
    nodes.append({
      "id": node_id, "type": "note", "title": f"Note {index:02d}",
      "description": f"Summary {index:02d} " + ("x" * 180),
      "path": path, "access_count": 0,
    })
    edges.append({"kind": "link", "source": "index", "target": node_id})
  (worktree / "graph.json").write_text(json.dumps({
    "nodes": nodes, "edges": edges, "problems": [],
  }), encoding="utf-8")
  return store.publish(worktree)


def _manifest(
  store, commit: str, count: int, *, chat_id="chat-1", lookup_id=None,
  node_ids: list[str] | None = None,
):
  lookup_id = lookup_id or ("a" * 64)
  node_ids = node_ids or [f"note-{index:02d}" for index in range(count)]
  candidates = [{
    "id": node_ids[index],
    "path": f"notes/{node_ids[index]}.md",
    "title": f"Note {index:02d}",
    "excerpt": f"Summary {index:02d} " + ("x" * 180),
  } for index in range(count)]
  trace = store.record_read(
    commit, "broad request", [item["path"] for item in candidates], chat_id,
    traversal={"opened": [], "selected": [item["path"] for item in candidates]},
    invocation_fingerprint=lookup_id,
  )
  now = datetime.now(UTC)
  value = {
    "schema": 3,
    "lookup_id": lookup_id,
    "status": "hit",
    "commit": commit,
    "chat_id": chat_id,
    "read_id": trace["read_id"],
    "cursor_secret": "c" * 64,
    "candidates": candidates,
    "expires_at": (now + timedelta(hours=48)).isoformat(),
    "discovery_complete": True,
  }
  with store.recall_execution(lookup_id) as receipt:
    receipt.store(value)
  return value


def _run(reader, *args):
  output = io.StringIO()
  with contextlib.redirect_stdout(output):
    code = reader.run(list(args))
  text = output.getvalue()
  marker = next(
    line for line in text.splitlines()
    if line.startswith("MOBIUS_APP_ACTIVITY_V1:")
  )
  payload = json.loads(marker.removeprefix("MOBIUS_APP_ACTIVITY_V1:"))
  return code, text, payload


def test_manifest_declared_reader_runs_as_a_direct_command():
  result = subprocess.run(
    [sys.executable, str(REPO / "memory_read.py"),
     "invalid", "catalog", "start", "chat-1"],
    check=False, capture_output=True, text=True,
  )

  assert result.returncode == 1
  marker = next(
    line for line in result.stdout.splitlines()
    if line.startswith("MOBIUS_APP_ACTIVITY_V1:")
  )
  payload = json.loads(marker.removeprefix("MOBIUS_APP_ACTIVITY_V1:"))
  assert payload["activity_id"] == "memory-read"
  assert payload["status"] == "failed"
  assert payload["reason"] == "invalid_lookup"


def test_catalogue_pages_every_candidate_without_a_fixed_count_cap():
  with tempfile.TemporaryDirectory() as raw:
    store, search, reader = _load(Path(raw))
    pointer = _publish(store, [f"Body {index}\n" for index in range(25)])
    manifest = _manifest(store, pointer["commit"], 25)
    result = search.RecallResult(
      search.RESULT_HIT,
      "Relevant Memory catalogue:",
      files=tuple(item["path"] for item in manifest["candidates"]),
      commit=pointer["commit"],
      notes=tuple(manifest["candidates"]),
      lookup_id=manifest["lookup_id"],
      read_id=manifest["read_id"],
      cursor_secret=manifest["cursor_secret"],
    )

    first_text = io.StringIO()
    with contextlib.redirect_stdout(first_text):
      assert search._emit_result(result) == 0
    first_marker = next(
      line for line in first_text.getvalue().splitlines()
      if line.startswith(search.RESULT_PREFIX)
    )
    payload = json.loads(first_marker.removeprefix(search.RESULT_PREFIX))
    seen = [item["id"] for item in payload["resources"]]
    operation_keys = {payload["operation_key"]}
    assert len(first_text.getvalue()) <= search.TOOL_OUTPUT_PAGE_CHARS

    while not payload["page"]["complete"]:
      code, text, payload = _run(
        reader, manifest["lookup_id"], "catalog",
        payload["page"]["next_cursor"], "chat-1",
      )
      assert code == 0
      assert len(text) <= search.TOOL_OUTPUT_PAGE_CHARS
      seen.extend(item["id"] for item in payload["resources"])
      operation_keys.add(payload["operation_key"])

    assert seen == [f"note-{index:02d}" for index in range(25)]
    # Every catalogue page is one operation, shown as one chat row.
    assert operation_keys == {f"{manifest['lookup_id']}:catalog"}
    assert not (store.STATE / "read-delivery" / f"{manifest['read_id']}.json").exists()
    assert not (store.STATE / "usage.json").exists()


def test_body_pages_reconstruct_exact_pinned_utf8_and_count_usage_once():
  with tempfile.TemporaryDirectory() as raw:
    store, _search, reader = _load(Path(raw))
    body = (
      "αβ🙂\nline with trailing spaces  \n\n"
      + ("🙂界" * 5_000)
      + "\nend\t \n"
    )
    pointer = _publish(store, [body])
    manifest = _manifest(store, pointer["commit"], 1)
    manifest["candidates"][0]["title"] = "界" * 120
    with store.recall_execution(manifest["lookup_id"]) as receipt:
      receipt.store(manifest)
    cursor = "start"
    rebuilt = bytearray()
    pages: list[tuple[str, str]] = []

    while True:
      code, text, payload = _run(
        reader, manifest["lookup_id"], '["note-00"]', cursor, "chat-1",
      )
      assert code == 0
      assert len(text.encode("utf-8")) <= reader.BODY_PAGE_BYTES
      frame = FRAME_RE.search(text)
      assert frame is not None
      metadata = json.loads(frame.group("meta"))
      chunk = frame.group("body").encode("utf-8")
      assert metadata["byte_start"] == len(rebuilt)
      assert metadata["byte_end"] - metadata["byte_start"] == len(chunk)
      assert metadata["sha256"] == hashlib.sha256(body.encode("utf-8")).hexdigest()
      rebuilt.extend(chunk)
      pages.append((cursor, text))
      if payload["page"]["complete"]:
        break
      cursor = payload["page"]["next_cursor"]

    assert bytes(rebuilt) == body.encode("utf-8")
    assert store.load_usage() == {"note-00": 1}
    delivery = store.load_read_delivery(manifest["read_id"])
    assert delivery["requested_files"] == ["notes/note-00.md"]
    assert delivery["fully_supplied_files"] == ["notes/note-00.md"]

    # Replaying any deterministic page merges the same byte range and cannot
    # count a second full delivery.
    code, _text, _payload = _run(
      reader, manifest["lookup_id"], '["note-00"]', pages[0][0], "chat-1",
    )
    assert code == 0
    assert store.load_usage() == {"note-00": 1}


def test_body_byte_budget_replaces_the_old_two_node_page_cap():
  with tempfile.TemporaryDirectory() as raw:
    store, _search, reader = _load(Path(raw))
    bodies = [(f"Note {index}\n" + ("x" * 900)) for index in range(9)]
    pointer = _publish(store, bodies)
    manifest = _manifest(store, pointer["commit"], len(bodies))

    code, text, payload = _run(
      reader, manifest["lookup_id"], "all", "start", "chat-1",
    )

    assert code == 0
    assert payload["page"]["complete"] is True
    assert len(payload["resources"]) == 9
    assert len(FRAME_RE.findall(text)) == 9


def test_every_page_of_one_read_shares_one_operation_key():
  # The chat folds rows sharing this key into one row, so a read that spans
  # pages shows every note it read under its "Finished reading" label.
  with tempfile.TemporaryDirectory() as raw:
    store, _search, reader = _load(Path(raw))
    bodies = [(f"Note {index}\n" + ("x" * 5_000)) for index in range(4)]
    pointer = _publish(store, bodies)
    manifest = _manifest(store, pointer["commit"], len(bodies))
    with store.recall_execution(manifest["lookup_id"]) as receipt:
      receipt.store(manifest)

    cursor, rows = "start", []
    while True:
      code, _text, payload = _run(
        reader, manifest["lookup_id"], "all", cursor, "chat-1",
      )
      assert code == 0
      rows.append(payload)
      if payload["page"]["complete"]:
        break
      cursor = payload["page"]["next_cursor"]

    assert len(rows) > 1
    keys = {row["operation_key"] for row in rows}
    assert len(keys) == 1
    assert next(iter(keys)).startswith(f"{manifest['lookup_id']}:read:")
    assert rows[-1]["label"] == "Finished reading 4 notes from Memory"
    delivered = [r["id"] for row in rows for r in row["resources"]]
    assert set(delivered) == {c["id"] for c in manifest["candidates"]}

    _code, _text, other = _run(
      reader, manifest["lookup_id"], json.dumps([manifest["candidates"][0]["id"]]),
      "start", "chat-1",
    )
    assert other["operation_key"] not in keys


def test_continuation_cursors_cannot_skip_undelivered_content():
  with tempfile.TemporaryDirectory() as raw:
    store, _search, reader = _load(Path(raw))
    pointer = _publish(store, ["x" * 20_000])
    manifest = _manifest(store, pointer["commit"], 1)
    code, _text, payload = _run(
      reader, manifest["lookup_id"], "all", "start", "chat-1",
    )
    assert code == 0 and payload["page"]["complete"] is False
    parts = payload["page"]["next_cursor"].split(":")
    parts[3] = str(int(parts[3]) + 100)
    code, _text, payload = _run(
      reader, manifest["lookup_id"], "all", ":".join(parts), "chat-1",
    )
    assert code == 1 and payload["reason"] == "invalid_cursor"


def test_every_publishable_node_id_remains_expandable():
  with tempfile.TemporaryDirectory() as raw:
    store, _search, reader = _load(Path(raw))
    long_id = "a" * 129
    pointer = _publish(store, ["long body", "ordinary body"], [long_id, "ordinary"])
    manifest = _manifest(
      store, pointer["commit"], 2, node_ids=[long_id, "ordinary"],
    )

    code, text, payload = _run(
      reader, manifest["lookup_id"], '["ordinary"]', "start", "chat-1",
    )

    assert code == 0
    assert payload["page"]["complete"] is True
    assert "ordinary body" in text


def test_read_rejects_cross_chat_expired_invalid_and_missing_pinned_content():
  with tempfile.TemporaryDirectory() as raw:
    store, _search, reader = _load(Path(raw))
    pointer = _publish(store, ["Pinned body\n"])
    manifest = _manifest(store, pointer["commit"], 1)

    code, _text, payload = _run(
      reader, manifest["lookup_id"], "all", "start", "other-chat",
    )
    assert code == 1 and payload["reason"] == "lookup_unavailable"
    code, _text, payload = _run(
      reader, manifest["lookup_id"], '["not-a-candidate"]', "start", "chat-1",
    )
    assert code == 1 and payload["reason"] == "invalid_selection"
    code, _text, payload = _run(
      reader, manifest["lookup_id"], "all",
      "body:deadbeefdeadbeef:0:0:" + ("0" * 32), "chat-1",
    )
    assert code == 1 and payload["reason"] == "invalid_cursor"

    manifest["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with store.recall_execution(manifest["lookup_id"]) as receipt:
      receipt.store(manifest)
    code, _text, payload = _run(
      reader, manifest["lookup_id"], "all", "start", "chat-1",
    )
    assert code == 1 and payload["reason"] == "lookup_unavailable"

    missing = _manifest(store, "f" * 40, 1, lookup_id="b" * 64)
    code, _text, payload = _run(
      reader, missing["lookup_id"], "all", "start", "chat-1",
    )
    assert code == 1 and payload["reason"] == "pinned_content_unavailable"
