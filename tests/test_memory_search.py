import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]


def _load(data_dir: Path):
  for name in ("memory_search", "memory_store"):
    sys.modules.pop(name, None)
  sys.path.insert(0, str(REPO))
  try:
    with mock.patch.dict(os.environ, {"DATA_DIR": str(data_dir)}):
      store = importlib.import_module("memory_store")
      search = importlib.import_module("memory_search")
  finally:
    sys.path.remove(str(REPO))
  return store, search


def _generation(store, *, title="Quiet interface", body="The user prefers a quiet interface."):
  seed = store.ROOT / "seed"
  (seed / "mocs").mkdir(parents=True, exist_ok=True)
  (seed / "notes").mkdir(exist_ok=True)
  (seed / "index.md").write_text("# Memory\n", encoding="utf-8")
  _, staging = store.start_staging(seed)
  (staging / "notes" / "quiet-ui.md").write_text(body + "\n", encoding="utf-8")
  graph = {
    "nodes": [{
      "id": "quiet-ui", "type": "note", "title": title,
      "description": "A durable interface preference", "tags": ["ui"],
      "path": "notes/quiet-ui.md", "access_count": 0,
    }],
    "edges": [], "problems": [],
  }
  (staging / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
  return store.publish(staging)


class MemorySearchContractTests(unittest.TestCase):
  def test_tool_free_subagent_selects_only_verified_catalog_paths(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, search = _load(Path(raw))
      catalog = [{
        "path": "notes/quiet-ui.md",
        "title": "Quiet interface",
        "description": "A durable preference",
        "tags": ["ui"],
      }]
      result = mock.Mock(
        returncode=0,
        stdout=json.dumps({
          "paths": ["../../owner-secret", "notes/quiet-ui.md"],
        }),
      )
      with (
        mock.patch.object(search, "_reader_provider", return_value="claude"),
        mock.patch.object(search.subprocess, "run", return_value=result) as run,
      ):
        paths = search._agent_paths("What interface style is preferred?", catalog)

      self.assertEqual(paths, ["notes/quiet-ui.md"])
      command = run.call_args.args[0]
      self.assertIn("--tools", command)
      self.assertEqual(command[command.index("--tools") + 1], "")
      self.assertNotIn("APP_TOKEN", run.call_args.kwargs["env"])

  def test_subagent_failure_falls_back_to_lexical_retrieval(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _generation(store)
      with mock.patch.object(search, "_agent_paths", return_value=[]):
        answer, files, _generation_id = search.retrieve("quiet interface")
      self.assertEqual(files, ["notes/quiet-ui.md"])
      self.assertIn("prefers a quiet interface", answer)

  def test_returns_only_confined_cited_text_and_records_app_telemetry(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      pointer = _generation(store)

      answer, files, generation = search.retrieve("Which quiet UI preferences matter?")

      self.assertEqual(generation, pointer["generation"])
      self.assertEqual(files, ["notes/quiet-ui.md"])
      self.assertIn("prefers a quiet interface", answer)
      self.assertIn("[notes/quiet-ui.md]", answer)

      old_argv = sys.argv
      sys.argv = [str(REPO / "memory_search.py"), "quiet UI preference", "chat-123"]
      out = io.StringIO()
      try:
        with contextlib.redirect_stdout(out):
          self.assertEqual(search.run(), 0)
      finally:
        sys.argv = old_argv
      self.assertIn("FILES: notes/quiet-ui.md", out.getvalue())
      trace = json.loads((store.STATE / "read-trace" / "chat-123.json").read_text())
      self.assertEqual(trace["generation"], pointer["generation"])
      self.assertEqual(trace["files"], ["notes/quiet-ui.md"])
      self.assertNotIn("quiet UI preference", json.dumps(trace))

  def test_malformed_pointer_returns_no_memory(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      store.ROOT.mkdir(parents=True)
      store.READY.write_text('{"schema":1,"generation":"../../secret"}', encoding="utf-8")

      answer, files, generation = search.retrieve("secret project")

      self.assertEqual((answer, files, generation), ("No relevant memories.", [], None))

  def test_symlinked_note_is_never_read_or_emitted(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      generation = "20260713T120000Z-aaaaaaaaaaaa"
      base = store.GENERATIONS / generation
      (base / "notes").mkdir(parents=True)
      outside = Path(raw) / "owner-secret.txt"
      outside.write_text("OWNER SECRET MUST NOT LEAK", encoding="utf-8")
      (base / "notes" / "quiet-ui.md").symlink_to(outside)
      (base / "graph.json").write_text(json.dumps({"nodes": [{
        "id": "quiet-ui", "title": "Secret project", "description": "secret",
        "path": "notes/quiet-ui.md",
      }]}), encoding="utf-8")
      store._atomic_text(store.READY, json.dumps({"schema": 1, "generation": generation}))

      answer, files, pinned = search.retrieve("secret project")

      self.assertEqual(pinned, generation)
      self.assertEqual(files, [])
      self.assertEqual(answer, "No relevant memories.")
      self.assertNotIn("OWNER SECRET", answer)

  def test_pointer_change_mid_read_does_not_mix_generations(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      old = _generation(store, body="Old pinned fact.")
      new = _generation(store, body="New replacement fact.")
      store._atomic_text(store.READY, json.dumps(old))
      original_read = search.read_generation_file
      switched = False

      def switching_read(generation, rel, **kwargs):
        nonlocal switched
        value = original_read(generation, rel, **kwargs)
        if rel == "graph.json" and not switched:
          switched = True
          store._atomic_text(store.READY, json.dumps(new))
        return value

      search.read_generation_file = switching_read
      answer, files, pinned = search.retrieve("quiet interface")

      self.assertEqual(pinned, old["generation"])
      self.assertEqual(files, ["notes/quiet-ui.md"])
      self.assertIn("Old pinned fact", answer)
      self.assertNotIn("New replacement fact", answer)


if __name__ == "__main__":
  unittest.main()
