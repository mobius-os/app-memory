import asyncio
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]


def _load(data_dir: Path):
  for name in ("memory_runner", "memory_graph", "memory_store"):
    sys.modules.pop(name, None)
  sys.path.insert(0, str(REPO))
  try:
    with mock.patch.dict(os.environ, {
      "DATA_DIR": str(data_dir), "APP_TOKEN": "scoped-app-token",
      "SERVICE_TOKEN": "must-not-be-forwarded", "AGENT_TOKEN": "also-secret",
    }):
      store = importlib.import_module("memory_store")
      runner = importlib.import_module("memory_runner")
  finally:
    sys.path.remove(str(REPO))
  return store, runner


def _seed(path: Path):
  (path / "mocs").mkdir(parents=True)
  (path / "notes").mkdir()
  (path / "index.md").write_text(
    "# Memory\n\n- [[maintaining-memory]]\n", encoding="utf-8",
  )
  (path / "mocs" / "maintaining-memory.md").write_text(
    "---\ntitle: Maintaining memory\ntype: moc\nmanaged_by: memory\n"
    "managed_schema: 1\n---\n# Maintaining memory\n\n"
    "- [[how-the-memory-graph-works]]\n",
    encoding="utf-8",
  )
  (path / "mocs" / "mobius-platform.md").write_text(
    "---\ntitle: The Mobius platform\ntype: moc\n---\n# Platform\n",
    encoding="utf-8",
  )
  (path / "mocs" / "about-the-user.md").write_text(
    "---\ntitle: About the user\ntype: moc\n---\n# About\n",
    encoding="utf-8",
  )
  (path / "mocs" / "building-mobius-apps.md").write_text(
    "---\ntitle: Building apps\ntype: moc\n---\n# Apps\n",
    encoding="utf-8",
  )
  (path / "notes" / "how-the-memory-graph-works.md").write_text(
    "---\ntitle: How the memory graph works\ntype: note\n"
    "managed_by: memory\nmanaged_schema: 1\n---\nManaged rules.\n",
    encoding="utf-8",
  )
  (path / "notes" / "memory-is-visible-to-the-partner.md").write_text(
    "---\ntitle: Memory is visible\ntype: note\n---\nVisible.\n",
    encoding="utf-8",
  )


def _proposal(chat_id="chat-1"):
  return {
    "summary": "promoted one preference",
    "followups": [],
    "deletes": [],
    "updates": [{
      "path": "notes/quiet-ui.md",
      "content": (
        "---\ntype: note\ntitle: Quiet UI\ndescription: Interface preference\n"
        f"source: [chat:{chat_id}]\n---\nThe user prefers quiet interfaces.\n"
      ),
    }],
  }


class MemoryRunnerTests(unittest.TestCase):
  def test_proposal_prompt_does_not_treat_assistant_completion_as_fact(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      prompt = runner._proposal_prompt(
        Path(raw),
        [{
          "id": "chat-1",
          "title": "Prototype",
          "updated_at": "2026-07-22T00:00:00Z",
          "messages": [{"role": "assistant", "text": "I implemented it."}],
        }],
      )

      self.assertIn("unverified testimony", prompt)
      self.assertIn("never promote", prompt)
      self.assertIn("partner confirms", prompt)

  def test_success_publishes_complete_commit_with_verified_provenance(self):
    with tempfile.TemporaryDirectory() as raw:
      store, runner = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      runner.SEED_DIR = seed
      runner._app_id = lambda: 7
      runner._app_active = lambda _app_id: True
      runner._redacted_chats = lambda: [{"id": "chat-1", "messages": []}]
      runner._proposal = lambda *_args: _proposal()

      self.assertEqual(asyncio.run(runner.run()), 0)

      pointer = store.ready_pointer()
      self.assertIsNotNone(pointer)
      note = store.read_revision_file(pointer["commit"], "notes/quiet-ui.md")
      graph = json.loads(store.read_revision_file(pointer["commit"], "graph.json"))
      self.assertIn("source: [chat:chat-1]", note)
      self.assertTrue(any(node["id"] == "quiet-ui" for node in graph["nodes"]))
      self.assertEqual(graph["problems"], [])
      self.assertTrue(any(node["id"] == "memory-unfiled" for node in graph["nodes"]))
      status = json.loads((store.STATE / "run-status.json").read_text())
      self.assertEqual(status["status"], "published")
      self.assertEqual(status["commit"], pointer["commit"])
      self.assertIn("specifically_reachable", status["topology"]["after"])

  def test_app_owned_docs_require_explicit_frontmatter_ownership(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      seed = Path(raw) / "seed"
      staging = Path(raw) / "staging"
      _seed(seed)
      _seed(staging)
      legacy_root = "# Legacy root injected automatically\n"
      legacy_moc = "# Legacy main-agent write rules\n"
      legacy_note = "# Legacy graph router injection\n"
      (staging / "index.md").write_text(legacy_root, encoding="utf-8")
      (staging / "mocs" / "maintaining-memory.md").write_text(
        legacy_moc, encoding="utf-8",
      )
      (staging / "notes" / "how-the-memory-graph-works.md").write_text(
        legacy_note, encoding="utf-8",
      )
      deprecated = "# Legacy cross-app coupling\n"
      (staging / "notes" / "deprecated.md").write_text(
        deprecated, encoding="utf-8",
      )
      changed, deleted = runner._reconcile_app_owned_docs(staging, seed)

      self.assertEqual(changed, [])
      self.assertEqual(deleted, [])
      self.assertEqual((staging / "index.md").read_text(), legacy_root)
      self.assertEqual((staging / "mocs" / "maintaining-memory.md").read_text(), legacy_moc)
      self.assertEqual((staging / "notes" / "how-the-memory-graph-works.md").read_text(), legacy_note)
      self.assertEqual((staging / "notes" / "deprecated.md").read_text(), deprecated)

      (staging / "mocs" / "maintaining-memory.md").unlink()
      explicit = (
        "---\ntitle: Old managed copy\nmanaged_by: memory\n---\nOld rules.\n"
      )
      (staging / "notes" / "how-the-memory-graph-works.md").write_text(
        explicit, encoding="utf-8",
      )
      changed, deleted = runner._reconcile_app_owned_docs(staging, seed)
      self.assertEqual(set(changed), {
        "mocs/maintaining-memory.md",
        "notes/how-the-memory-graph-works.md",
      })
      self.assertEqual(deleted, [])
      self.assertEqual(
        (staging / "mocs" / "maintaining-memory.md").read_text(),
        (seed / "mocs" / "maintaining-memory.md").read_text(),
      )
      self.assertEqual(
        (staging / "notes" / "how-the-memory-graph-works.md").read_text(),
        (seed / "notes" / "how-the-memory-graph-works.md").read_text(),
      )

      custom = "# Partner-custom root\n"
      (staging / "index.md").write_text(custom, encoding="utf-8")
      custom_deprecated = "# Partner-custom note at the old path\n"
      (staging / "notes" / "deprecated.md").write_text(
        custom_deprecated, encoding="utf-8",
      )
      changed, deleted = runner._reconcile_app_owned_docs(staging, seed)
      self.assertNotIn("index.md", changed)
      self.assertEqual(deleted, [])
      self.assertEqual((staging / "index.md").read_text(), custom)
      self.assertEqual(
        (staging / "notes" / "deprecated.md").read_text(), custom_deprecated,
      )

      body_marker_only = "# Partner notes\n\nmanaged_by: memory\n"
      (staging / "mocs" / "maintaining-memory.md").write_text(
        body_marker_only, encoding="utf-8",
      )
      changed, _deleted = runner._reconcile_app_owned_docs(staging, seed)
      self.assertNotIn("mocs/maintaining-memory.md", changed)
      self.assertEqual(
        (staging / "mocs" / "maintaining-memory.md").read_text(),
        body_marker_only,
      )

  def test_orphan_repair_covers_disconnected_cycles_and_is_stable(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      staging = Path(raw) / "staging"
      _seed(staging)
      (staging / "notes" / "cycle-a.md").write_text(
        "# A\n\n[[cycle-b]]\n", encoding="utf-8",
      )
      (staging / "notes" / "cycle-b.md").write_text(
        "# B\n\n[[cycle-a]]\n", encoding="utf-8",
      )

      first_graph = runner.build_graph(staging, usage={})
      initial_orphans = {
        p["node"] for p in first_graph["problems"] if p["kind"] == "orphan"
      }
      self.assertTrue({"cycle-a", "cycle-b"}.issubset(initial_orphans))
      runner._repair_orphans(staging, first_graph)
      self.assertEqual(runner.build_graph(staging, usage={})["problems"], [])

      (staging / "notes" / "later.md").write_text("# Later\n", encoding="utf-8")
      runner._repair_orphans(staging, runner.build_graph(staging, usage={}))
      final_graph = runner.build_graph(staging, usage={})
      self.assertEqual(final_graph["problems"], [])
      unfiled = (staging / "mocs" / "memory-unfiled.md").read_text()
      self.assertIn("[[cycle-a]]", unfiled)
      self.assertIn("[[cycle-b]]", unfiled)
      self.assertIn("[[later]]", unfiled)

  def test_structural_lints_surface_as_warnings_not_errors(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      staging = Path(raw) / "staging"
      (staging / "mocs").mkdir(parents=True)
      (staging / "notes").mkdir()
      # A fully linked graph so orphan/dangling errors cannot mask the lints;
      # the split-candidate warnings must be the only problems reported.
      (staging / "index.md").write_text(
        "---\ntitle: Home\ntype: moc\n---\n# Home\n\n- [[map]]\n",
        encoding="utf-8",
      )
      (staging / "mocs" / "map.md").write_text(
        "---\ntitle: Map\ntype: moc\n---\n# Map\n\n"
        "- [[sprawling]]\n- [[filed-nowhere]]\n",
        encoding="utf-8",
      )
      # 40 prose lines > MAX_NOTE_PROSE_LINES (30) -> oversized_note.
      body = "\n".join(f"Distinct claim number {i}." for i in range(40))
      (staging / "notes" / "sprawling.md").write_text(
        "---\ntitle: Sprawling\ntype: note\nmocs: [map]\n---\n" + body + "\n",
        encoding="utf-8",
      )
      # mocs points at a map that does not exist -> bare_map_entry.
      (staging / "notes" / "filed-nowhere.md").write_text(
        "---\ntitle: Filed nowhere\ntype: note\nmocs: [ghost-map]\n---\nShort.\n",
        encoding="utf-8",
      )

      graph = runner.build_graph(staging, usage={})

      by_kind = {p["kind"]: p for p in graph["problems"]}
      self.assertIn("oversized_note", by_kind)
      self.assertEqual(by_kind["oversized_note"]["node"], "sprawling")
      self.assertGreater(by_kind["oversized_note"]["lines"], 30)
      self.assertIn("bare_map_entry", by_kind)
      self.assertEqual(by_kind["bare_map_entry"]["node"], "filed-nowhere")
      # Every reported problem is a warning, so none of them block publication
      # under the same predicate the runner uses.
      for problem in graph["problems"]:
        self.assertEqual(problem["severity"], "warning", problem)
      blocking = [
        p for p in graph["problems"] if p.get("severity") != "warning"
      ]
      self.assertEqual(blocking, [])

  def test_overfull_map_is_flagged_as_a_split_candidate(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      staging = Path(raw) / "staging"
      (staging / "mocs").mkdir(parents=True)
      (staging / "notes").mkdir()
      entries = "".join(f"- [[note-{i}]]\n" for i in range(31))
      (staging / "index.md").write_text(
        "---\ntitle: Home\ntype: moc\n---\n# Home\n\n- [[map]]\n",
        encoding="utf-8",
      )
      (staging / "mocs" / "map.md").write_text(
        "---\ntitle: Map\ntype: moc\n---\n# Map\n\n" + entries,
        encoding="utf-8",
      )
      for i in range(31):
        (staging / "notes" / f"note-{i}.md").write_text(
          f"---\ntitle: Note {i}\ntype: note\nmocs: [map]\n---\nBody.\n",
          encoding="utf-8",
        )

      problems = runner.build_graph(staging, usage={})["problems"]

      overfull = [p for p in problems if p["kind"] == "overfull_map"]
      self.assertEqual(len(overfull), 1)
      self.assertEqual(overfull[0]["node"], "map")
      self.assertEqual(overfull[0]["severity"], "warning")
      self.assertGreater(overfull[0]["entries"], 30)

  def test_invalid_model_provenance_fails_without_advancing_pointer(self):
    with tempfile.TemporaryDirectory() as raw:
      store, runner = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      _, first = store.start_staging(seed)
      runner.build_graph(first, usage={})
      old = store.publish(first)
      runner.SEED_DIR = seed
      runner._app_id = lambda: 7
      runner._app_active = lambda _app_id: True
      runner._redacted_chats = lambda: [{"id": "chat-1", "messages": []}]
      runner._proposal = lambda *_args: _proposal("unseen-chat")

      self.assertEqual(asyncio.run(runner.run()), 1)

      self.assertEqual(store.ready_pointer()["commit"], old["commit"])
      self.assertEqual(store._git("status", "--porcelain", text=True).stdout, "")
      status = json.loads((store.STATE / "run-status.json").read_text())
      self.assertEqual(status["error_code"], "unverified_chat_provenance")
      self.assertEqual(status["offending_path"], "notes/quiet-ui.md")
      self.assertEqual(status["invalid_source_count"], 1)
      pending = json.loads(runner._PENDING_CHAT_IDS.read_text())
      self.assertEqual(pending["chat_ids"], ["chat-1"])

  def test_claude_child_gets_no_platform_or_app_credentials(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      captured = {}

      class FakePopen:
        pid = 999998
        returncode = 0

        def __init__(self, cmd, **kwargs):
          captured.update({"cmd": cmd, **kwargs})

        def communicate(self, value=None, timeout=None):
          captured["input"] = value
          captured["timeout"] = timeout
          return '{"updates":[]}', ""

      with mock.patch.object(runner.subprocess, "Popen", FakePopen):
        value = runner._claude_proposal({"provider": "claude", "effort": "ultracode"}, "prompt")

      self.assertEqual(value, {"updates": []})
      self.assertIn("--tools", captured["cmd"])
      self.assertEqual(captured["cmd"][captured["cmd"].index("--tools") + 1], "")
      self.assertEqual(captured["cmd"][captured["cmd"].index("--effort") + 1], "xhigh")
      self.assertEqual(captured["input"], "prompt")
      self.assertNotIn("prompt", captured["cmd"])
      self.assertTrue(captured["start_new_session"])
      for key in ("APP_TOKEN", "SERVICE_TOKEN", "AGENT_TOKEN", "API_BASE_URL", "DATA_DIR"):
        self.assertNotIn(key, captured["env"])
      self.assertTrue(captured["cwd"].startswith("/tmp/memory-agent-"))

  def test_claude_effort_allows_reviewed_values_and_omits_unknown_values(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      commands = []

      class FakePopen:
        pid = 999998
        returncode = 0

        def __init__(self, cmd, **_kwargs):
          commands.append(cmd)

        def communicate(self, value=None, timeout=None):
          return '{"updates":[]}', ""

      with mock.patch.object(runner.subprocess, "Popen", FakePopen):
        runner._claude_proposal({"provider": "claude", "effort": "max"}, "prompt")
        runner._claude_proposal({"provider": "claude", "effort": "future-level"}, "prompt")

      self.assertEqual(commands[0][commands[0].index("--effort") + 1], "max")
      self.assertNotIn("--effort", commands[1])

  def test_agent_timeout_kills_and_reaps_the_whole_process_session(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))

      class TimedOutPopen:
        pid = 123456
        returncode = -9
        calls = 0

        def __init__(self, _cmd, **_kwargs):
          pass

        def communicate(self, _value=None, timeout=None):
          self.calls += 1
          if self.calls == 1:
            raise subprocess.TimeoutExpired("agent", timeout)
          return "", ""

      with (
        mock.patch.object(runner.subprocess, "Popen", TimedOutPopen),
        mock.patch.object(runner.os, "killpg") as killpg,
      ):
        result = runner._run_text_process(
          ["agent"], "prompt", cwd=raw, env={"PATH": "/usr/bin"},
        )

      self.assertIsNone(result)
      killpg.assert_called_once_with(123456, runner.signal.SIGKILL)
      self.assertEqual(runner._ACTIVE_AGENT_GROUPS, set())

  def test_shutdown_signal_kills_every_active_agent_group(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      runner._ACTIVE_AGENT_GROUPS.update({123456, 234567})
      try:
        with mock.patch.object(runner, "_kill_agent_group") as kill_group:
          with self.assertRaises(SystemExit) as raised:
            runner._terminate_active_agents(runner.signal.SIGTERM, None)
        self.assertEqual(raised.exception.code, 128 + runner.signal.SIGTERM)
        self.assertEqual(
          {call.args[0] for call in kill_group.call_args_list},
          {123456, 234567},
        )
      finally:
        runner._ACTIVE_AGENT_GROUPS.clear()

  def test_codex_child_is_ephemeral_read_only_and_gets_no_credentials(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      captured = {}

      class FakePopen:
        pid = 999999
        returncode = 0

        def __init__(self, cmd, **kwargs):
          captured.update({"cmd": cmd, **kwargs})

        def communicate(self, value=None, timeout=None):
          captured["input"] = value
          captured["timeout"] = timeout
          event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": '{"updates":[]}'},
          }
          return json.dumps(event) + "\n", ""

      with (
        mock.patch.object(runner.shutil, "which", return_value="/usr/bin/codex"),
        mock.patch.object(runner.subprocess, "Popen", FakePopen),
      ):
        value = runner._codex_proposal(
          {"provider": "codex", "model": "gpt-test", "effort": "high"},
          "prompt",
        )

      self.assertEqual(value, {"updates": []})
      self.assertEqual(captured["input"], "prompt")
      self.assertIn("--ephemeral", captured["cmd"])
      self.assertIn("--ignore-user-config", captured["cmd"])
      self.assertEqual(
        captured["cmd"][captured["cmd"].index("--sandbox") + 1], "read-only",
      )
      self.assertIn("shell_tool", captured["cmd"])
      self.assertIn("apps", captured["cmd"])
      for key in ("APP_TOKEN", "SERVICE_TOKEN", "AGENT_TOKEN", "API_BASE_URL", "DATA_DIR"):
        self.assertNotIn(key, captured["env"])
      self.assertTrue(captured["cwd"].startswith("/tmp/memory-agent-"))

  def test_degraded_provider_run_is_visible_and_does_not_publish(self):
    with tempfile.TemporaryDirectory() as raw:
      store, runner = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      _, first = store.start_staging(seed)
      runner.build_graph(first, usage={})
      old = store.publish(first)
      runner.SEED_DIR = seed
      runner._app_id = lambda: 7
      runner._app_active = lambda _app_id: True
      runner._redacted_chats = lambda: []
      runner._proposal = lambda *_args: runner.ProposalOutcome(
        status="degraded", proposal=None, provider=None, model=None,
        attempted_agents=[{
          "provider": "codex", "model": "gpt-test", "supported": True,
        }],
      )

      self.assertEqual(asyncio.run(runner.run()), 2)
      self.assertEqual(store.ready_pointer()["commit"], old["commit"])
      status = json.loads((store.STATE / "run-status.json").read_text())
      self.assertEqual(status["status"], "degraded")
      self.assertEqual(status["commit"], old["commit"])
      self.assertEqual(status["reason"], "no_valid_text_only_proposal")
      run_log = next((store.STATE / "run-log").glob("*.jsonl")).read_text()
      events = [json.loads(line) for line in run_log.splitlines()]
      self.assertEqual([event["status"] for event in events], ["running", "degraded"])

  def test_topology_regression_fails_before_unfiled_can_hide_it(self):
    with tempfile.TemporaryDirectory() as raw:
      store, runner = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      _, first = store.start_staging(seed)
      runner.build_graph(first, usage={})
      old = store.publish(first)
      runner.SEED_DIR = seed
      runner._app_id = lambda: 7
      runner._app_active = lambda _app_id: True
      runner._redacted_chats = lambda: []
      runner._proposal = lambda *_args: {
        "summary": "replace the root", "followups": [], "deletes": [],
        "updates": [{"path": "index.md", "content": "# Empty root\n"}],
      }

      self.assertEqual(asyncio.run(runner.run()), 1)
      self.assertEqual(store.ready_pointer()["commit"], old["commit"])
      status = json.loads((store.STATE / "run-status.json").read_text())
      self.assertEqual(status["status"], "failed")
      self.assertEqual(status["error_class"], "ValueError")

  def test_proposal_data_is_bounded_valid_json(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      staging = Path(raw) / "staging"
      staging.mkdir()
      chats = [{
        "id": f"chat-{index}", "title": "A" * 500,
        "messages": [
          {"role": "user", "text": "x" * 2_000} for _ in range(200)
        ],
      } for index in range(30)]

      encoded = runner._proposal_data(staging, chats)
      value = json.loads(encoded)

      self.assertLessEqual(len(encoded), runner._MAX_PROMPT_DATA_CHARS)
      self.assertTrue(value["redacted_recent_chats"])
      self.assertLess(len(value["redacted_recent_chats"]), len(chats))

  def test_proposal_data_exposes_short_handles_not_canonical_chat_ids(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      staging = Path(raw) / "staging"
      staging.mkdir()
      canonical = "1f905105-a3a6-4a67-a6e3-1b34ea6963d8"

      data = json.loads(runner._proposal_data(staging, [{
        "id": canonical, "title": "Capability work", "messages": [],
      }]))

      row = data["redacted_recent_chats"][0]
      self.assertEqual(row["source_handle"], "chat:c01")
      self.assertNotIn("id", row)
      self.assertNotIn(canonical, json.dumps(data))

  def test_short_source_handle_is_expanded_before_publication(self):
    with tempfile.TemporaryDirectory() as raw:
      store, runner = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      runner.SEED_DIR = seed
      runner._app_id = lambda: 7
      runner._app_active = lambda _app_id: True
      canonical = "1f905105-a3a6-4a67-a6e3-1b34ea6963d8"
      runner._redacted_chats = lambda: [{"id": canonical, "messages": []}]
      runner._proposal = lambda *_args: _proposal("c01")
      runner._remember_pending_chats([{"id": canonical}])

      self.assertEqual(asyncio.run(runner.run()), 0)

      note = store.read_revision_file(
        store.ready_pointer()["commit"], "notes/quiet-ui.md",
      )
      self.assertIn(f"source: [chat:{canonical}]", note)
      self.assertNotIn("chat:c01", note)
      self.assertFalse(runner._PENDING_CHAT_IDS.exists())

  def test_success_acknowledges_only_chats_that_fit_the_prompt(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      staging = Path(raw) / "staging"
      staging.mkdir()
      runner._MAX_PROMPT_DATA_CHARS = 500
      chats = [
        {"id": "first", "title": "first", "messages": [{"role": "user", "text": "a"}]},
        {"id": "second", "title": "second", "messages": [{"role": "user", "text": "b" * 1000}]},
      ]
      runner._remember_pending_chats(chats)

      offered = runner._proposal_batch(staging, chats)
      runner._acknowledge_pending_chats(offered)

      self.assertEqual([chat["id"] for chat in offered], ["first"])
      self.assertEqual(runner._load_pending_chat_ids(), ["second"])

  def test_failed_run_chat_ids_are_retried_before_latest_window(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      runner._remember_pending_chats([{"id": "pending-chat"}])

      def fake_api(path):
        if path.startswith("/api/chat-logs?"):
          return {"items": [
            {"id": "latest-chat"}, {"id": "pending-chat"},
          ]}
        chat_id = path.rsplit("/", 1)[-1]
        return {"title": chat_id, "messages": []}

      runner._api_json = fake_api

      chats = runner._redacted_chats(limit=30)

      self.assertEqual(
        [chat["id"] for chat in chats],
        ["pending-chat", "latest-chat"],
      )

  def test_latest_id_is_queued_even_when_its_detail_fetch_fails(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))

      def fake_api(path):
        if path.startswith("/api/chat-logs?"):
          return {"items": [{"id": "temporarily-unreadable"}]}
        return None

      runner._api_json = fake_api

      self.assertEqual(runner._redacted_chats(), [])
      self.assertEqual(
        runner._load_pending_chat_ids(),
        ["temporarily-unreadable"],
      )

  def test_default_discovery_window_covers_a_full_proposal_batch(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      listing_paths = []
      ids = [f"chat-{index:03d}" for index in range(75)]

      def fake_api(path):
        if path.startswith("/api/chat-logs?"):
          listing_paths.append(path)
          return {"items": [{"id": chat_id} for chat_id in ids]}
        chat_id = path.rsplit("/", 1)[-1]
        return {"title": chat_id, "messages": []}

      runner._api_json = fake_api

      chats = runner._redacted_chats()

      self.assertEqual([chat["id"] for chat in chats], ids)
      self.assertIn("limit=100", listing_paths[0])
      self.assertEqual(runner._load_pending_chat_ids(), ids)

  def test_full_discovery_window_still_retries_older_pending_chats(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      pending = [f"pending-{index:03d}" for index in range(70)]
      latest = [f"latest-{index:03d}" for index in range(100)]
      runner._remember_pending_chats([{"id": chat_id} for chat_id in pending])

      def fake_api(path):
        if path.startswith("/api/chat-logs?"):
          return {"items": [{"id": chat_id} for chat_id in latest]}
        chat_id = path.rsplit("/", 1)[-1]
        return {"title": chat_id, "messages": []}

      runner._api_json = fake_api

      chats = runner._redacted_chats()

      self.assertEqual(
        [chat["id"] for chat in chats],
        pending + latest[:30],
      )

  def test_semantically_invalid_primary_proposal_uses_configured_fallback(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      staging = Path(raw) / "staging"
      _seed(staging)
      runner.build_graph(staging, usage={})
      canonical = "1f905105-a3a6-4a67-a6e3-1b34ea6963d8"
      runner._agent_choices = lambda _app_id: [
        {"provider": "claude", "model": "primary"},
        {"provider": "codex", "model": "fallback"},
      ]
      runner._claude_proposal = lambda *_args: _proposal("invented-source")
      runner._codex_proposal = lambda *_args: _proposal("c01")

      outcome = runner._proposal(7, staging, [{"id": canonical, "messages": []}])

      self.assertEqual(outcome.status, "ok")
      self.assertEqual(outcome.provider, "codex")
      self.assertEqual(
        outcome.attempted_agents[0]["rejection_code"],
        "unverified_chat_provenance",
      )
      self.assertIn(
        f"source: [chat:{canonical}]",
        outcome.proposal["updates"][0]["content"],
      )

  def test_semantically_invalid_only_provider_degrades_with_reason_code(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      staging = Path(raw) / "staging"
      _seed(staging)
      runner.build_graph(staging, usage={})
      runner._agent_choices = lambda _app_id: [
        {"provider": "claude", "model": "only"},
      ]
      runner._claude_proposal = lambda *_args: _proposal("invented-source")

      outcome = runner._proposal(7, staging, [{"id": "chat-1", "messages": []}])

      self.assertEqual(outcome.status, "degraded")
      self.assertIsNone(outcome.proposal)
      self.assertEqual(
        outcome.attempted_agents[0]["rejection_code"],
        "unverified_chat_provenance",
      )

  def test_existing_content_is_available_for_alignment_and_old_provenance_survives(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      staging = Path(raw) / "staging"
      _seed(staging)
      old = (
        "---\ntype: note\ntitle: Old fact\ndescription: prior fact\n"
        "source: [chat:old-chat]\n---\nThe durable old detail.\n"
      )
      (staging / "notes" / "old.md").write_text(old, encoding="utf-8")
      runner.build_graph(staging, usage={})

      data = json.loads(runner._proposal_data(staging, []))
      old_row = next(row for row in data["existing_graph"] if row["path"] == "notes/old.md")
      self.assertIn("durable old detail", old_row["content"])
      changed, deleted = runner._apply_proposal(
        staging,
        {
          "updates": [{"path": "notes/old.md", "content": old.replace("detail", "detail, aligned")}],
          "deletes": [],
        },
        allowed_chat_ids=runner._known_chat_sources(staging),
      )
      self.assertEqual((changed, deleted), (["notes/old.md"], []))

  def test_validated_delete_can_merge_duplicate_without_touching_index(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      staging = Path(raw) / "staging"
      _seed(staging)
      duplicate = staging / "notes" / "duplicate.md"
      duplicate.write_text("old duplicate", encoding="utf-8")

      changed, deleted = runner._apply_proposal(
        staging, {"updates": [], "deletes": ["notes/duplicate.md"]},
        allowed_chat_ids=set(),
      )
      self.assertEqual((changed, deleted), ([], ["notes/duplicate.md"]))
      self.assertFalse(duplicate.exists())
      with self.assertRaisesRegex(ValueError, "deletion"):
        runner._apply_proposal(
          staging, {"updates": [], "deletes": ["index.md"]},
          allowed_chat_ids=set(),
        )
      with self.assertRaisesRegex(ValueError, "deletion"):
        runner._apply_proposal(
          staging, {"updates": [], "deletes": ["mocs/memory-unfiled.md"]},
          allowed_chat_ids=set(),
        )
      with self.assertRaisesRegex(ValueError, "deletion"):
        runner._apply_proposal(
          staging,
          {"updates": [], "deletes": ["mocs/maintaining-memory.md"]},
          allowed_chat_ids=set(),
        )

  def test_duplicate_note_and_moc_ids_cannot_be_published(self):
    with tempfile.TemporaryDirectory() as raw:
      store, runner = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      (seed / "mocs" / "same.md").write_text("# MOC\n", encoding="utf-8")
      (seed / "notes" / "same.md").write_text("# Note\n", encoding="utf-8")
      runner.SEED_DIR = seed
      runner._app_id = lambda: 7
      runner._app_active = lambda _app_id: True
      runner._redacted_chats = lambda: []
      runner._proposal = lambda *_args: {
        "summary": "no provider", "followups": [], "updates": [], "deletes": [],
      }

      self.assertEqual(asyncio.run(runner.run()), 1)
      self.assertIsNone(store.ready_pointer())
      self.assertFalse((store.REPOSITORY / ".git" / "index.lock").exists())

  def test_liveness_uses_reviewed_system_capabilities_not_slug(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      runner._api_json = lambda _path: {
        "id": 7,
        "slug": "memory-2",
        "system_app": True,
        "capability_contract": {
          "data": {"shared_memory": "write"},
          "background": {"agent": True},
        },
      }

      self.assertTrue(runner._app_active(7))
      self.assertFalse(runner._app_active(8))

  def test_agent_choices_apply_canonical_and_legacy_app_overrides(self):
    with tempfile.TemporaryDirectory() as raw:
      data_dir = Path(raw)
      _store, runner = _load(data_dir)
      settings_dir = data_dir / "apps" / "7"
      settings_dir.mkdir(parents=True)
      runner._api_json = lambda _path: {
        "primary": {"provider": "claude", "model": "system-primary"},
        "fallback": {"provider": "codex", "model": "system-fallback"},
      }
      (settings_dir / "settings.json").write_text(json.dumps({
        "primary_agent_mode": "custom",
        "provider": "codex",
        "model": "gpt-custom",
        "secondary_agent_mode": "app",
        "fallback_provider": "claude",
        "fallback_model": "claude-custom",
      }))

      self.assertEqual(runner._agent_choices(7), [
        {"provider": "codex", "model": "gpt-custom", "effort": None},
        {"provider": "claude", "model": "claude-custom", "effort": None},
      ])

  def test_agent_choices_deduplicate_exact_identity_but_keep_distinct_effort(self):
    with tempfile.TemporaryDirectory() as raw:
      data_dir = Path(raw)
      _store, runner = _load(data_dir)
      settings_dir = data_dir / "apps" / "7"
      settings_dir.mkdir(parents=True)
      runner._api_json = lambda _path: {
        "primary": {"provider": "claude", "model": "system-primary", "effort": "medium"},
        "fallback": {"provider": "codex", "model": "same", "effort": "high"},
      }
      settings = {
        "primary_agent_mode": "app",
        "provider": "codex",
        "model": "same",
        "effort": "high",
        "secondary_agent_mode": "system",
      }
      path = settings_dir / "settings.json"
      path.write_text(json.dumps(settings))
      self.assertEqual(runner._agent_choices(7), [
        {"provider": "codex", "model": "same", "effort": "high"},
      ])

      settings["effort"] = "medium"
      path.write_text(json.dumps(settings))
      self.assertEqual(runner._agent_choices(7), [
        {"provider": "codex", "model": "same", "effort": "medium"},
        {"provider": "codex", "model": "same", "effort": "high"},
      ])


if __name__ == "__main__":
  unittest.main()
