import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import threading
import time
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
  (seed / "index.md").write_text("# Memory\n\n- [[quiet-ui]]\n", encoding="utf-8")
  _, staging = store.start_staging(seed)
  (staging / "notes" / "quiet-ui.md").write_text(body + "\n", encoding="utf-8")
  graph = {
    "nodes": [
      {
        "id": "index", "type": "index", "title": "Memory",
        "description": "Root memory routes", "path": "index.md",
      },
      {
        "id": "quiet-ui", "type": "note", "title": title,
        "description": "A durable interface preference", "tags": ["ui"],
        "path": "notes/quiet-ui.md", "access_count": 0,
      },
    ],
    "edges": [{"kind": "link", "source": "index", "target": "quiet-ui"}],
    "problems": [],
  }
  (staging / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
  return store.publish(staging)


class MemorySearchContractTests(unittest.TestCase):
  def test_same_physical_turn_reuses_one_provider_result_and_one_read_record(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _commit(store)
      calls = 0
      original = search._retrieve_prepared

      def counted(request):
        nonlocal calls
        calls += 1
        return original(request)

      outputs = []
      old_argv = sys.argv
      try:
        with (
          mock.patch.dict(os.environ, {
            "MOBIUS_RUN_TOKEN": "physical-turn-a",
            "MOBIUS_DELEGATION_ID": "",
            "MEMORY_READER_PROVIDER": "none",
          }),
          mock.patch.object(search, "_retrieve_prepared", side_effect=counted),
        ):
          sys.argv = [
            str(REPO / "memory_search.py"), "quiet interface", "chat-1",
          ]
          for _ in range(2):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
              self.assertEqual(search.run(), 0)
            outputs.append(output.getvalue())
      finally:
        sys.argv = old_argv

      self.assertEqual(calls, 1)
      self.assertNotIn('"reused":true', outputs[0])
      self.assertIn('"reused":true', outputs[1])
      self.assertIn("prefers a quiet interface", outputs[1])
      log = next((store.STATE / "read-log").glob("*.jsonl"))
      self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 1)
      self.assertEqual(
        json.loads((store.STATE / "usage.json").read_text()),
        {"quiet-ui": 1},
      )
      receipt = next((store.STATE / "recall-execution").glob("*.json"))
      self.assertNotIn("physical-turn-a", receipt.read_text(encoding="utf-8"))

  def test_identical_wording_in_a_later_physical_turn_is_a_new_read(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _commit(store)
      calls = 0
      original = search._retrieve_prepared

      def counted(request):
        nonlocal calls
        calls += 1
        return original(request)

      old_argv = sys.argv
      try:
        with (
          mock.patch.dict(os.environ, {"MEMORY_READER_PROVIDER": "none"}),
          mock.patch.object(search, "_retrieve_prepared", side_effect=counted),
        ):
          sys.argv = [
            str(REPO / "memory_search.py"), "quiet interface", "chat-1",
          ]
          for token in ("physical-turn-a", "physical-turn-b"):
            with mock.patch.dict(os.environ, {"MOBIUS_RUN_TOKEN": token}):
              with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(search.run(), 0)
      finally:
        sys.argv = old_argv

      self.assertEqual(calls, 2)
      log = next((store.STATE / "read-log").glob("*.jsonl"))
      self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 2)

  def test_calls_without_a_physical_turn_identity_are_not_coalesced(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _commit(store)
      calls = 0
      original = search._retrieve_prepared

      def counted(request):
        nonlocal calls
        calls += 1
        return original(request)

      with (
        mock.patch.dict(os.environ, {
          "MOBIUS_RUN_TOKEN": "",
          "MOBIUS_DELEGATION_ID": "",
          "MEMORY_READER_PROVIDER": "none",
        }),
        mock.patch.object(search, "_retrieve_prepared", side_effect=counted),
      ):
        # Prepare after masking the identity so this exercises the legacy and
        # direct-script contract rather than manufacturing a shared lifetime.
        request = search._prepare_request("quiet interface", "chat-1")
        search._execute_request(request)
        search._execute_request(request)

      self.assertIsNone(request.invocation_fingerprint)
      self.assertEqual(calls, 2)
      log = next((store.STATE / "read-log").glob("*.jsonl"))
      self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 2)

  def test_concurrent_identical_retries_share_one_in_flight_lookup(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _commit(store)
      original = search._retrieve_prepared
      started = threading.Event()
      release = threading.Event()
      count_lock = threading.Lock()
      calls = 0

      def slow(request):
        nonlocal calls
        with count_lock:
          calls += 1
        started.set()
        self.assertTrue(release.wait(timeout=3))
        return original(request)

      results = []
      with (
        mock.patch.dict(os.environ, {
          "MOBIUS_RUN_TOKEN": "one-concurrent-turn",
          "MEMORY_READER_PROVIDER": "none",
        }),
        mock.patch.object(search, "_retrieve_prepared", side_effect=slow),
      ):
        request = search._prepare_request("quiet interface", "chat-1")
        threads = [
          threading.Thread(
            target=lambda: results.append(search._execute_request(request)),
          )
          for _ in range(2)
        ]
        threads[0].start()
        self.assertTrue(started.wait(timeout=2))
        threads[1].start()
        time.sleep(0.1)
        release.set()
        for thread in threads:
          thread.join(timeout=5)
          self.assertFalse(thread.is_alive())

      self.assertEqual(calls, 1)
      self.assertEqual(len(results), 2)
      self.assertEqual(sum(result.reused for result in results), 1)
      log = next((store.STATE / "read-log").glob("*.jsonl"))
      self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 1)

  def test_failed_reads_are_not_sticky_inside_the_same_turn(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _commit(store)
      calls = 0

      def failed(_request):
        nonlocal calls
        calls += 1
        return search.RecallResult(
          search.RESULT_FAILED,
          "Memory lookup failed.",
          reason=search.RESULT_REASON_READ_FAILED,
        )

      with (
        mock.patch.dict(os.environ, {"MOBIUS_RUN_TOKEN": "repairable-turn"}),
        mock.patch.object(search, "_retrieve_prepared", side_effect=failed),
      ):
        request = search._prepare_request("quiet interface", "chat-1")
        first = search._execute_request(request)
        second = search._execute_request(request)

      self.assertEqual(calls, 2)
      self.assertFalse(first.reused)
      self.assertFalse(second.reused)
      self.assertFalse(next((store.STATE / "recall-execution").glob("*.json")).read_text())

  def test_completed_result_is_not_repeated_when_receipt_storage_fails(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _commit(store)
      calls = 0
      original = search._retrieve_prepared

      def counted(request):
        nonlocal calls
        calls += 1
        return original(request)

      with (
        mock.patch.dict(os.environ, {
          "MOBIUS_RUN_TOKEN": "receipt-write-failure",
          "MEMORY_READER_PROVIDER": "none",
        }),
        mock.patch.object(search, "_retrieve_prepared", side_effect=counted),
        mock.patch.object(store.RecallExecution, "store", side_effect=OSError),
      ):
        request = search._prepare_request("quiet interface", "chat-1")
        with contextlib.redirect_stderr(io.StringIO()) as errors:
          result = search._execute_request(request)

      self.assertEqual(result.status, search.RESULT_HIT)
      self.assertEqual(calls, 1)
      self.assertIn("could not be stored", errors.getvalue())

  def test_telemetry_failure_is_repaired_without_repeating_provider_work(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _commit(store)
      calls = 0
      record_attempts = 0
      original_retrieve = search._retrieve_prepared
      original_record = store.record_read

      def counted(request):
        nonlocal calls
        calls += 1
        return original_retrieve(request)

      def flaky_record(*args, **kwargs):
        nonlocal record_attempts
        record_attempts += 1
        if record_attempts == 1:
          raise OSError("temporary telemetry failure")
        return original_record(*args, **kwargs)

      with (
        mock.patch.dict(os.environ, {
          "MOBIUS_RUN_TOKEN": "telemetry-repair-turn",
          "MEMORY_READER_PROVIDER": "none",
        }),
        mock.patch.object(search, "_retrieve_prepared", side_effect=counted),
        mock.patch.object(search, "record_read", side_effect=flaky_record),
        contextlib.redirect_stderr(io.StringIO()),
      ):
        request = search._prepare_request("quiet interface", "chat-1")
        first = search._execute_request(request)
        second = search._execute_request(request)

      self.assertEqual(calls, 1)
      self.assertEqual(record_attempts, 2)
      self.assertFalse(first.reused)
      self.assertTrue(second.reused)
      log = next((store.STATE / "read-log").glob("*.jsonl"))
      self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 1)
      with store.recall_execution(request.invocation_fingerprint) as execution:
        self.assertTrue(execution.load()["recorded"])

  def test_latest_trace_repairs_crash_window_without_another_provider_call(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      commit = _commit(store)["commit"]
      with mock.patch.dict(os.environ, {
        "MOBIUS_RUN_TOKEN": "crash-window-turn",
        "MEMORY_READER_PROVIDER": "none",
      }):
        request = search._prepare_request("quiet interface", "chat-1")
        store.record_read(
          commit,
          request.question,
          ["notes/quiet-ui.md"],
          request.chat_id,
          invocation_fingerprint=request.invocation_fingerprint,
        )
        with mock.patch.object(
          search, "_retrieve_prepared",
          side_effect=AssertionError("provider must not run"),
        ):
          result = search._execute_request(request)

      self.assertTrue(result.reused)
      self.assertEqual(result.files, ("notes/quiet-ui.md",))
      with store.recall_execution(request.invocation_fingerprint) as execution:
        self.assertEqual(execution.load()["files"], ["notes/quiet-ui.md"])

  def test_tampered_execution_receipt_cannot_escape_the_pinned_graph(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _commit(store)
      calls = 0
      original = search._retrieve_prepared

      def counted(request):
        nonlocal calls
        calls += 1
        return original(request)

      with (
        mock.patch.dict(os.environ, {
          "MOBIUS_RUN_TOKEN": "tampered-receipt-turn",
          "MEMORY_READER_PROVIDER": "none",
        }),
        mock.patch.object(search, "_retrieve_prepared", side_effect=counted),
      ):
        request = search._prepare_request("quiet interface", "chat-1")
        with store.recall_execution(request.invocation_fingerprint) as execution:
          execution.store({
            "schema": 1,
            "status": search.RESULT_HIT,
            "commit": request.commit,
            "files": ["../../owner-secret.md"],
          })
        result = search._execute_request(request)

      self.assertEqual(calls, 1)
      self.assertFalse(result.reused)
      self.assertEqual(result.files, ("notes/quiet-ui.md",))
      with store.recall_execution(request.invocation_fingerprint) as execution:
        self.assertEqual(execution.load()["files"], ["notes/quiet-ui.md"])

  def test_selector_change_invalidates_same_turn_reuse_key(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, search = _load(Path(raw))
      with mock.patch.dict(os.environ, {"MOBIUS_RUN_TOKEN": "one-turn"}):
        first = search._invocation_fingerprint(
          "same question", "chat-1", "0" * 40,
          {"schema": 1, "run_id": "night", "instruction": "first lesson"},
        )
        second = search._invocation_fingerprint(
          "same question", "chat-1", "0" * 40,
          {"schema": 1, "run_id": "night", "instruction": "new lesson"},
        )
      self.assertNotEqual(first, second)

  def test_navigator_can_open_only_host_verified_linked_nodes(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _commit(store)
      actions = iter([
        {
          "finish": False,
          "expand": [{
            "from": "index", "nodes": ["../../owner-secret", "quiet-ui"],
          }],
          "selected": [],
        },
        {
          "finish": True, "expand": [],
          "selected": ["../../owner-secret", "quiet-ui"],
        },
      ])
      with mock.patch.object(
        search, "_live_text_call",
        return_value=lambda _prompt: json.dumps(next(actions)),
      ):
        result = search.retrieve("What interface style is preferred?")

      self.assertEqual(result.status, search.RESULT_HIT)
      self.assertEqual(result.files, ("notes/quiet-ui.md",))
      self.assertNotIn("owner-secret", result.answer)
      self.assertEqual(
        [node.path for node in result.traversal.opened],
        ["index.md", "notes/quiet-ui.md"],
      )

  def test_provider_failure_falls_back_to_lexical_graph_traversal(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _commit(store)
      with mock.patch.object(search, "_live_text_call", return_value=None):
        result = search.retrieve("quiet interface")
      self.assertEqual(result.status, search.RESULT_HIT)
      self.assertEqual(result.files, ("notes/quiet-ui.md",))
      self.assertIn("prefers a quiet interface", result.answer)
      self.assertTrue(all(
        decision["source"] == "lexical_fallback"
        for decision in result.traversal.decisions
      ))

  def test_real_empty_model_verdict_is_not_padded_by_lexical_results(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _commit(store)
      with mock.patch.object(
        search,
        "_live_text_call",
        return_value=lambda _prompt: json.dumps({
          "finish": True,
          "expand": [],
          "selected": [],
          "reason": "No opened node records the requested fact.",
        }),
      ):
        result = search.retrieve("quiet interface")

      self.assertEqual(result.status, search.RESULT_EMPTY)
      self.assertEqual(result.files, ())
      self.assertEqual(result.traversal.decisions[-1]["source"], "model")

  def test_exact_tokens_do_not_treat_plan_as_platform(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
      _commit(
        store,
        title="Möbius platform update",
        body="The platform update is durable.",
      )
      with mock.patch.object(search, "_live_text_call", return_value=None):
        result = search.retrieve("meal plan")

      self.assertEqual(result.status, search.RESULT_EMPTY)
      self.assertEqual((result.answer, result.files), ("No relevant memories.", ()))

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
      self.assertEqual(trace["question"], "quiet UI preference")
      self.assertEqual(trace["traversal"]["selected"], ["notes/quiet-ui.md"])
      self.assertEqual(trace["traversal"]["opened"][0]["path"], "index.md")

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

      actions = iter([
        {
          "finish": False,
          "expand": [{"from": "index", "nodes": ["quiet-ui"]}],
          "selected": [],
        },
      ])
      with (
        mock.patch.object(search, "read_revision_file", side_effect=reject_note),
        mock.patch.object(
          search, "_live_text_call",
          return_value=lambda _prompt: json.dumps(next(actions)),
        ),
      ):
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

      with (
        mock.patch.object(search, "read_revision_file", side_effect=switching_read),
        mock.patch.dict(os.environ, {"MEMORY_READER_PROVIDER": "none"}),
      ):
        result = search.retrieve("quiet interface")

      self.assertEqual(result.commit, old["commit"])
      self.assertEqual(result.files, ("notes/quiet-ui.md",))
      self.assertIn("Old pinned fact", result.answer)
      self.assertNotIn("New replacement fact", result.answer)

  def test_missing_graph_is_an_explicit_failed_result(self):
    with tempfile.TemporaryDirectory() as raw:
      store, search = _load(Path(raw))
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
        {
          "status": search.RESULT_FAILED,
          "reason": search.RESULT_REASON_NOT_READY,
        },
      )
      trace = json.loads(
        (store.STATE / "read-trace" / "chat-1.json").read_text()
      )
      self.assertEqual(trace["status"], "failed")
      self.assertEqual(trace["reason"], search.RESULT_REASON_NOT_READY)
      self.assertIsNone(trace["commit"])
      self.assertFalse((store.STATE / "read-log").exists())

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
