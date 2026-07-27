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


def _commit(store, *, title="Quiet interface", body="The user prefers a quiet interface."):
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

  def test_tool_free_subagent_cannot_pad_a_focused_recall(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, search = _load(Path(raw))
      catalog = [
        {
          "path": f"notes/note-{index}.md",
          "title": f"Note {index}",
          "description": "A possible memory",
          "tags": [],
        }
        for index in range(8)
      ]
      result = mock.Mock(
        returncode=0,
        stdout=json.dumps({"paths": [item["path"] for item in catalog]}),
      )
      with (
        mock.patch.object(search, "_reader_provider", return_value="claude"),
        mock.patch.object(search.subprocess, "run", return_value=result) as run,
      ):
        paths = search._agent_paths("What changed in this narrow feature?", catalog)

      self.assertEqual(paths, [item["path"] for item in catalog[:4]])
      prompt = run.call_args.args[0][2]
      self.assertIn("SMALLEST sufficient set", prompt)
      self.assertIn("do not fill the quota", prompt)

  def test_subagent_failure_falls_back_to_lexical_retrieval(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _commit(store)
      with mock.patch.object(search, "_agent_paths", return_value=None):
        result = search.retrieve("quiet interface")
      self.assertEqual(result.status, search.RESULT_HIT)
      self.assertEqual(result.files, ("notes/quiet-ui.md",))
      self.assertIn("prefers a quiet interface", result.answer)

  def test_a_real_empty_semantic_verdict_is_not_padded_by_lexical_results(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _commit(store)
      # The lexical ranker has a strong hit, but a selector that actually ran
      # and returned [] made a semantic judgement. Do not replace that verdict
      # with the old automatic top-four fallback.
      with mock.patch.object(search, "_agent_paths", return_value=[]):
        result = search.retrieve("quiet interface")

      self.assertEqual(result.status, search.RESULT_EMPTY)
      self.assertEqual(result.files, ())

  def test_semantic_selector_never_receives_unrelated_quota_fillers(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _commit(
        store,
        title="Forge is the 3D printing tool",
        body="Forge is used for 3D printing.",
      )
      with mock.patch.object(search, "_agent_paths") as select:
        result = search.retrieve(
          "What did we previously decide about a Daily Landing that helps "
          "the partner feel less scattered?",
        )

      self.assertEqual(result.status, search.RESULT_EMPTY)
      self.assertEqual((result.answer, result.files), ("No relevant memories.", ()))
      select.assert_called_once_with(
        mock.ANY,
        [],
      )

  def test_exact_tokens_do_not_treat_plan_as_platform(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _commit(
        store,
        title="Möbius platform update",
        body="The platform update is durable.",
      )
      with mock.patch.object(search, "_agent_paths", return_value=None):
        result = search.retrieve("meal plan")

      self.assertEqual(result.status, search.RESULT_EMPTY)
      self.assertEqual((result.answer, result.files), ("No relevant memories.", ()))

  def test_codex_selector_is_confined_and_parses_only_agent_messages(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, search = _load(Path(raw))
      stdout = "\n".join([
        json.dumps({"type": "item.completed", "item": {
          "type": "command_execution", "text": "ignore me",
        }}),
        json.dumps({"type": "item.completed", "item": {
          "type": "agent_message", "text": '{"paths":[]}',
        }}),
      ])
      result = mock.Mock(returncode=0, stdout=stdout)
      with (
        mock.patch.dict(os.environ, {"CODEX_CLI_PATH": "/usr/bin/codex"}),
        mock.patch.object(search.subprocess, "run", return_value=result) as run,
      ):
        text = search._codex_select_text("choose notes")

      self.assertEqual(text, '{"paths":[]}')
      command = run.call_args.args[0]
      self.assertIn("read-only", command)
      for feature in (
        "shell_tool", "apps", "browser_use", "computer_use",
        "multi_agent", "image_generation", "goals",
      ):
        self.assertIn(feature, command)
      self.assertEqual(run.call_args.kwargs["input"], "choose notes")
      self.assertNotIn("AGENT_TOKEN", run.call_args.kwargs["env"])

  def test_auto_reader_uses_codex_when_claude_is_unavailable(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, search = _load(Path(raw))
      with (
        mock.patch.dict(os.environ, {"MEMORY_READER_PROVIDER": "auto"}),
        mock.patch.object(search, "_claude_available", return_value=False),
        mock.patch.object(search, "_codex_available", return_value=True),
      ):
        self.assertEqual(search._reader_provider(), "codex")

  def test_returns_only_confined_cited_text_and_records_app_telemetry(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      pointer = _commit(store)

      result = search.retrieve("Which quiet UI preferences matter?")

      self.assertEqual(result.status, search.RESULT_HIT)
      self.assertEqual(result.commit, pointer["commit"])
      self.assertEqual(result.files, ("notes/quiet-ui.md",))
      self.assertIn("prefers a quiet interface", result.answer)
      self.assertIn("[notes/quiet-ui.md]", result.answer)

      old_argv = sys.argv
      sys.argv = [str(REPO / "memory_search.py"), "quiet UI preference", "chat-123"]
      out = io.StringIO()
      try:
        with contextlib.redirect_stdout(out):
          self.assertEqual(search.run(), 0)
      finally:
        sys.argv = old_argv
      self.assertIn("FILES: notes/quiet-ui.md", out.getvalue())
      marker = next(
        line for line in out.getvalue().splitlines()
        if line.startswith(search.RESULT_PREFIX)
      )
      payload = json.loads(marker.removeprefix(search.RESULT_PREFIX))
      self.assertEqual(payload["status"], search.RESULT_HIT)
      self.assertEqual(payload["notes"][0]["path"], "notes/quiet-ui.md")
      trace = json.loads((store.STATE / "read-trace" / "chat-123.json").read_text())
      self.assertEqual(trace["commit"], pointer["commit"])
      self.assertEqual(trace["files"], ["notes/quiet-ui.md"])
      self.assertNotIn("quiet UI preference", json.dumps(trace))

  def test_run_requires_the_query_and_chat_id_contract_exactly(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, search = _load(Path(raw))
      old_argv = sys.argv
      try:
        for args in ([], ["query"], ["query", "chat-1", "extra"]):
          sys.argv = [str(REPO / "memory_search.py"), *args]
          err = io.StringIO()
          with contextlib.redirect_stderr(err):
            self.assertEqual(search.run(), 2)
          self.assertIn('"<chat_id>"', err.getvalue())
      finally:
        sys.argv = old_argv

  def test_malformed_pointer_returns_no_memory(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      store.ROOT.mkdir(parents=True)
      store.READY.write_text('{"schema":2,"commit":"../../secret"}', encoding="utf-8")

      result = search.retrieve("secret project")

      self.assertEqual(result.status, search.RESULT_FAILED)
      self.assertEqual((result.files, result.commit), ((), None))

  def test_symlinked_note_is_never_read_or_emitted(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _commit(store, title="Secret project", body="safe fact")
      original_read = search.read_revision_file

      def reject_note(commit, rel, **kwargs):
        if rel == "notes/quiet-ui.md":
          raise ValueError("unsafe memory source")
        return original_read(commit, rel, **kwargs)

      with mock.patch.object(search, "read_revision_file", side_effect=reject_note):
        result = search.retrieve("secret project")

      self.assertEqual(result.status, search.RESULT_FAILED)
      self.assertEqual(result.files, ())

  def test_pointer_change_mid_read_does_not_mix_commits(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      old = _commit(store, body="Old pinned fact.")
      new = _commit(store, body="New replacement fact.")
      store._atomic_text(store.READY, json.dumps(old))
      original_read = search.read_revision_file
      switched = False

      def switching_read(commit, rel, **kwargs):
        nonlocal switched
        value = original_read(commit, rel, **kwargs)
        if rel == "graph.json" and not switched:
          switched = True
          store._atomic_text(store.READY, json.dumps(new))
        return value

      with mock.patch.object(search, "read_revision_file", side_effect=switching_read):
        result = search.retrieve("quiet interface")

      self.assertEqual(result.commit, old["commit"])
      self.assertEqual(result.files, ("notes/quiet-ui.md",))
      self.assertIn("Old pinned fact", result.answer)
      self.assertNotIn("New replacement fact", result.answer)

  def test_missing_graph_is_an_explicit_failed_result(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, search = _load(Path(raw))
      old_argv = sys.argv
      sys.argv = [str(REPO / "memory_search.py"), "quiet interface", "chat-1"]
      out = io.StringIO()
      try:
        with contextlib.redirect_stdout(out):
          self.assertEqual(search.run(), 1)
      finally:
        sys.argv = old_argv

      marker = next(
        line for line in out.getvalue().splitlines()
        if line.startswith(search.RESULT_PREFIX)
      )
      self.assertEqual(
        json.loads(marker.removeprefix(search.RESULT_PREFIX)),
        {"status": search.RESULT_FAILED},
      )

  def test_corrupt_graph_is_failure_but_valid_no_match_is_empty(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      pointer = _commit(store)
      original_read = search.read_revision_file

      def corrupt_graph(commit, rel, **kwargs):
        if rel == "graph.json":
          return "{not-json"
        return original_read(commit, rel, **kwargs)

      with mock.patch.object(search, "read_revision_file", side_effect=corrupt_graph):
        failed = search.retrieve("quiet interface")
      self.assertEqual(failed.status, search.RESULT_FAILED)

      def malformed_graph(commit, rel, **kwargs):
        if rel == "graph.json":
          return '{"nodes":"not-a-list"}'
        return original_read(commit, rel, **kwargs)

      with mock.patch.object(search, "read_revision_file", side_effect=malformed_graph):
        malformed = search.retrieve("quiet interface")
      self.assertEqual(malformed.status, search.RESULT_FAILED)

      empty = search.retrieve("a subject absent from every note")
      self.assertEqual(empty.status, search.RESULT_EMPTY)
      self.assertEqual(empty.commit, pointer["commit"])


if __name__ == "__main__":
  unittest.main()
