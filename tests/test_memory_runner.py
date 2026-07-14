import asyncio
import hashlib
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

  def test_app_owned_docs_migrate_exact_legacy_bytes_but_preserve_custom_root(self):
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
      runner._LEGACY_MANAGED_SHA256 = {
        "index.md": {hashlib.sha256(legacy_root.encode()).hexdigest()},
        "mocs/maintaining-memory.md": {
          hashlib.sha256(legacy_moc.encode()).hexdigest(),
        },
        "notes/how-the-memory-graph-works.md": {
          hashlib.sha256(legacy_note.encode()).hexdigest(),
        },
      }
      runner._LEGACY_DELETE_SHA256 = {
        "notes/deprecated.md": {
          hashlib.sha256(deprecated.encode()).hexdigest(),
        },
      }

      changed, deleted = runner._reconcile_app_owned_docs(staging, seed)

      self.assertEqual(
        set(changed),
        {"index.md", "mocs/maintaining-memory.md", "notes/how-the-memory-graph-works.md"},
      )
      self.assertEqual(deleted, ["notes/deprecated.md"])
      self.assertFalse((staging / "notes" / "deprecated.md").exists())
      self.assertEqual(
        (staging / "index.md").read_text(), (seed / "index.md").read_text(),
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

  def test_claude_child_gets_no_platform_or_app_credentials(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      captured = {}

      def fake_run(cmd, **kwargs):
        captured.update({"cmd": cmd, **kwargs})
        return subprocess.CompletedProcess(cmd, 0, '{"updates":[]}', "")

      with mock.patch.object(runner.subprocess, "run", side_effect=fake_run):
        value = runner._claude_proposal({"provider": "claude"}, "prompt")

      self.assertEqual(value, {"updates": []})
      self.assertIn("--tools", captured["cmd"])
      self.assertEqual(captured["cmd"][captured["cmd"].index("--tools") + 1], "")
      for key in ("APP_TOKEN", "SERVICE_TOKEN", "AGENT_TOKEN", "API_BASE_URL", "DATA_DIR"):
        self.assertNotIn(key, captured["env"])
      self.assertTrue(captured["cwd"].startswith("/tmp/memory-agent-"))

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


if __name__ == "__main__":
  unittest.main()
