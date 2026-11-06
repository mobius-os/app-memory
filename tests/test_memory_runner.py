import asyncio
import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
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


def _reviewed(proposal):
  return {
    **proposal,
    "self_review": {
      "hardest_decision": "Which detail was durable.",
      "possibly_missed": "none",
      "prompt_change": "none",
      "next_experiment": "none",
    },
  }


def _proposal(chat_id="chat-1"):
  return _reviewed({
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
  })


def _shared_seed(path: Path):
  """A clean graph whose maps share notes, as long-lived graphs do."""
  _seed(path)
  (path / "notes" / "memory-is-visible-to-the-partner.md").unlink()
  (path / "index.md").write_text(
    "# Memory\n\n- [[maintaining-memory]] — how Memory works\n"
    "- [[about-the-user]] — who the partner is\n"
    "- [[building-mobius-apps]] — what they build\n"
    "- [[mobius-platform]] — where it runs\n",
    encoding="utf-8",
  )
  maps = {
    "about-the-user": ("About the user", ["contributes-code", "self-hosts"]),
    "building-mobius-apps": ("Building apps", ["contributes-code"]),
    "mobius-platform": ("The Mobius platform", ["self-hosts"]),
  }
  for slug, (title, members) in maps.items():
    (path / "mocs" / f"{slug}.md").write_text(
      f"---\ntitle: {title}\ntype: moc\n---\n# {title}\n\n"
      + "".join(f"- [[{member}]] — {member} cue\n" for member in members),
      encoding="utf-8",
    )
  for slug, mocs in (
    ("contributes-code", "about-the-user, building-mobius-apps"),
    ("self-hosts", "about-the-user, mobius-platform"),
  ):
    (path / "notes" / f"{slug}.md").write_text(
      f"---\ntype: note\ntitle: {slug}\ndescription: The partner {slug}.\n"
      f"mocs: [{mocs}]\n---\nThe partner {slug}.\n",
      encoding="utf-8",
    )


def _standing_writer(
  runner, calls: list, *, fact_map: str = "building-mobius-apps",
  fact_followups: tuple = ("OPEN: self-hosts may need a fresh as-of.",),
):
  """A writer like the live one: it changes nothing but restates, for every
  map it reviews, a standing follow-up naming each note it holds. A saved
  fact is filed as a new note under `fact_map`."""
  def writer(_app_id, _staging, batch, _audits, *_rest, **kwargs):
    item = kwargs.get("consolidation")
    if item is not None:
      moc = item["moc"]["path"]
      calls.append(moc)
      members = ", ".join(Path(path).stem for path in item["member_paths"])
      proposal = {
        "summary": "clean neighborhood",
        "followups": [f"STANDING ({Path(moc).stem}): keep {members} as they are."],
        "updates": [], "deletes": [],
      }
    else:
      chat_id = batch[0]["id"]
      calls.append("chat")
      proposal = {
        "summary": "filed one saved fact",
        "followups": list(fact_followups),
        "updates": [{
          "path": f"notes/saved-fact-{chat_id}.md",
          "content": (
            f"---\ntype: note\ntitle: Saved fact {chat_id}\n"
            f"description: A preference from {chat_id}.\n"
            f"mocs: [{fact_map}]\nsource: [chat:{chat_id}]\n---\n"
            f"A preference from {chat_id}.\n"
          ),
        }],
        "links": [{
          "from": f"mocs/{fact_map}.md",
          "to": f"notes/saved-fact-{chat_id}.md",
          "cue": "a saved preference",
        }],
        "deletes": [],
      }
    return runner.ProposalOutcome("ok", _reviewed(proposal), "test", None, [])
  return writer


class MemoryRunnerTests(unittest.TestCase):
  def test_a_quiet_night_publishes_without_a_writer_and_keeps_its_leads(self):
    with tempfile.TemporaryDirectory() as raw:
      store, runner = _load(Path(raw))
      seed = Path(raw) / "seed"
      _shared_seed(seed)
      runner.SEED_DIR = seed
      runner._app_id = lambda: 7
      runner._app_active = lambda _app_id: True
      runner._collect_capture_intake = lambda: runner.CaptureIntake([])
      calls: list = []
      runner._proposal = _standing_writer(runner, calls)

      self.assertEqual(asyncio.run(runner.run()), 0)
      self.assertTrue(calls, "the first night reviews every new map")
      update_log = store.STATE / "update-log"
      logged = {path.name: path.read_text() for path in update_log.glob("*.jsonl")}
      calls.clear()

      self.assertEqual(asyncio.run(runner.run()), 0)

      self.assertEqual(calls, [])
      status = json.loads((store.STATE / "run-status.json").read_text())
      self.assertEqual(status["status"], "published")
      self.assertIsNone(status["provider"])
      self.assertEqual(status["proposal_batch_count"], 0)
      self.assertEqual(status["model_work"]["attempt_count"], 0)
      self.assertEqual(status["commit"], store.ready_pointer()["commit"])
      # The writer's log, and the follow-ups it holds, are left as they were.
      self.assertEqual(
        {path.name: path.read_text() for path in update_log.glob("*.jsonl")},
        logged,
      )

  def test_a_night_of_agreed_lookups_records_them_without_a_writer(self):
    with tempfile.TemporaryDirectory() as raw:
      store, runner = _load(Path(raw))
      seed = Path(raw) / "seed"
      _shared_seed(seed)
      runner.SEED_DIR = seed
      runner._app_id = lambda: 7
      runner._app_active = lambda _app_id: True
      runner._collect_capture_intake = lambda: runner.CaptureIntake([])
      calls: list = []
      runner._proposal = _standing_writer(runner, calls)
      self.assertEqual(asyncio.run(runner.run()), 0)
      calls.clear()

      # One lookup since, which the deep replay fully agreed with.
      audit = {
        "read_id": "agreed", "at": "2026-10-30T10:00:00+00:00",
        "question": "Does the partner self-host?",
        "live": {"selected": ["notes/self-hosts.md"]},
        "deep": {"selected": ["notes/self-hosts.md"], "missed": [], "overreach": []},
      }
      runner._pending_read_traces = lambda: [{"read_id": "agreed"}]
      runner._deep_replays = lambda *_args: []
      runner._audit_reads = lambda *_args: [audit]
      recorded = []
      runner._record_recall_audits = (
        lambda _run, audits, proposal, _graph: recorded.append((audits, proposal))
      )

      self.assertEqual(asyncio.run(runner.run()), 0)

      self.assertEqual(calls, [])
      status = json.loads((store.STATE / "run-status.json").read_text())
      self.assertEqual(status["status"], "published")
      self.assertEqual(status["agreed_read_audit_count"], 1)
      [(audits, proposal)] = recorded
      self.assertEqual([item["read_id"] for item in audits], ["agreed"])
      self.assertEqual(
        [(v["read_id"], v["verdict_source"]) for v in proposal["read_audits"]],
        [("agreed", "deep_replay")],
      )

  def test_standing_followups_cannot_keep_the_writer_busy_on_quiet_nights(self):
    with tempfile.TemporaryDirectory() as raw:
      store, runner = _load(Path(raw))
      seed = Path(raw) / "seed"
      _shared_seed(seed)
      runner.SEED_DIR = seed
      runner._app_id = lambda: 7
      runner._app_active = lambda _app_id: True
      runner._collect_archived_source_lifecycle = lambda *_args: ([], set())
      runner._fetch_chat_detail = lambda _chat_id: (None, None)
      night = {"n": 0}
      runner._collect_capture_intake = lambda: runner.CaptureIntake(
        [{"id": "chat-5", "messages": []}] if night["n"] == 5 else [],
      )
      calls: list = []
      runner._proposal = _standing_writer(runner, calls)

      writer_calls = {}
      for n in range(1, 11):
        night["n"] = n
        calls.clear()
        self.assertEqual(asyncio.run(runner.run()), 0, f"night {n}")
        status = json.loads((store.STATE / "run-status.json").read_text())
        self.assertEqual(status["status"], "published", f"night {n}")
        writer_calls[n] = list(calls)

      self.assertTrue(writer_calls[1], "the first night reviews every new map")
      # Restated standing notes about shared facts wake nothing by themselves.
      for n in (2, 3, 4, 8, 9, 10):
        self.assertEqual(writer_calls[n], [], f"night {n} had nothing new")
      # A saved fact is filed. The next night reviews, once each, the map it
      # changed and the maps holding self-hosts, which its follow-up named.
      self.assertEqual(writer_calls[5], ["chat"])
      self.assertEqual(sorted(writer_calls[6]), [
        "mocs/about-the-user.md", "mocs/building-mobius-apps.md",
        "mocs/mobius-platform.md",
      ])
      # Those reviews restate their standing notes, which wake nothing.
      self.assertEqual(writer_calls[7], [])
      self.assertTrue(store.read_revision_file(
        store.ready_pointer()["commit"], "notes/saved-fact-chat-5.md",
      ))

  def test_daily_saved_facts_do_not_let_map_reviews_wake_their_neighbors(self):
    with tempfile.TemporaryDirectory() as raw:
      store, runner = _load(Path(raw))
      seed = Path(raw) / "seed"
      _shared_seed(seed)
      runner.SEED_DIR = seed
      runner._app_id = lambda: 7
      runner._app_active = lambda _app_id: True
      runner._collect_archived_source_lifecycle = lambda *_args: ([], set())
      runner._fetch_chat_detail = lambda _chat_id: (None, None)
      night = {"n": 0}
      runner._collect_capture_intake = lambda: runner.CaptureIntake(
        [{"id": f"chat-{night['n']}", "messages": []}] if night["n"] > 1 else [],
      )
      calls: list = []
      runner._proposal = _standing_writer(
        runner, calls, fact_map="mobius-platform", fact_followups=(),
      )

      reviewed = []
      for n in range(1, 13):
        night["n"] = n
        calls.clear()
        self.assertEqual(asyncio.run(runner.run()), 0, f"night {n}")
        reviewed.extend(call for call in calls if n > 1 and call != "chat")

      # Facts arrive every night, but only the map they are filed under is
      # reviewed; the standing notes those reviews restate about shared
      # notes never wake about-the-user or building-mobius-apps.
      self.assertTrue(reviewed)
      self.assertEqual(set(reviewed), {"mocs/mobius-platform.md"})

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
      runner._collect_capture_intake = lambda: runner.CaptureIntake([{"id": "chat-1", "messages": []}])
      runner._proposal = lambda *_args, **_kwargs: runner.ProposalOutcome(
        "ok", _proposal(), "test", None, [],
      )

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

  def test_first_install_publishes_seed_before_agent_review_finishes(self):
    with tempfile.TemporaryDirectory() as raw:
      store, runner = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      runner.SEED_DIR = seed
      runner._app_id = lambda: 7
      runner._app_active = lambda _app_id: True
      runner._collect_capture_intake = lambda: runner.CaptureIntake([])
      proposal_started = threading.Event()
      finish_proposal = threading.Event()

      def slow_proposal(*_args, **_kwargs):
        proposal_started.set()
        if not finish_proposal.wait(5):
          raise TimeoutError("test did not release proposal")
        proposal = {
          "summary": "nothing durable yet",
          "followups": [],
          "updates": [],
          "deletes": [],
          "self_review": _proposal()["self_review"],
        }
        return runner.ProposalOutcome("ok", proposal, "test", None, [])

      runner._proposal = slow_proposal

      async def exercise():
        task = asyncio.create_task(runner.run())
        try:
          self.assertTrue(
            await asyncio.to_thread(proposal_started.wait, 5),
            "agent review never started",
          )
          self.assertFalse(task.done())
          pointer = store.ready_pointer()
          self.assertIsNotNone(pointer)
          graph = json.loads(
            store.read_revision_file(pointer["commit"], "graph.json")
          )
          self.assertGreater(len(graph["nodes"]), 0)
          self.assertFalse([
            problem for problem in graph["problems"]
            if problem.get("severity") != "warning"
          ])
        finally:
          finish_proposal.set()
        self.assertEqual(await task, 0)
        status = json.loads((store.STATE / "run-status.json").read_text())
        self.assertIsNone(status["previous_commit"])
        self.assertTrue(status["new_commit"])

      asyncio.run(exercise())

  def test_first_install_keeps_seed_when_agent_review_is_degraded(self):
    with tempfile.TemporaryDirectory() as raw:
      store, runner = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      runner.SEED_DIR = seed
      runner._app_id = lambda: 7
      runner._app_active = lambda _app_id: True
      runner._collect_capture_intake = lambda: runner.CaptureIntake([])
      runner._proposal = lambda *_args, **_kwargs: runner.ProposalOutcome(
        status="degraded",
        proposal=None,
        provider=None,
        model=None,
        attempted_agents=[],
      )

      self.assertEqual(asyncio.run(runner.run()), 2)
      pointer = store.ready_pointer()
      self.assertIsNotNone(pointer)
      graph = json.loads(
        store.read_revision_file(pointer["commit"], "graph.json")
      )
      self.assertGreater(len(graph["nodes"]), 0)
      status = json.loads((store.STATE / "run-status.json").read_text())
      self.assertEqual(status["status"], "degraded")
      self.assertIsNone(status["previous_commit"])
      self.assertEqual(status["commit"], pointer["commit"])

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
      # A fully linked graph so orphan/dangling errors cannot mask the lints.
      (staging / "index.md").write_text(
        "---\ntitle: Home\ntype: moc\n---\n# Home\n\n- [[map]]\n",
        encoding="utf-8",
      )
      (staging / "mocs" / "map.md").write_text(
        "---\ntitle: Map\ntype: moc\n---\n# Map\n\n"
        "- [[sprawling]]\n- [[filed-nowhere]]\n",
        encoding="utf-8",
      )
      body = "\n".join(f"Distinct claim number {i}." for i in range(40))
      (staging / "notes" / "sprawling.md").write_text(
        "---\ntitle: Sprawling\ntype: note\nmocs: [map]\n---\n" + body + "\n",
        encoding="utf-8",
      )
      # mocs points at a map that does not exist -> bare_map_entry.
      (staging / "notes" / "filed-nowhere.md").write_text(
        "---\ntitle: Filed nowhere\ntype: note\nmocs: [ghost-map]\n"
        "source: [chat:missing]\n---\nShort.\n",
        encoding="utf-8",
      )
      (staging / "mocs" / "map.md").write_text(
        "---\ntitle: Map\ntype: moc\n---\n# Map\n\n"
        "- [[sprawling]]\n- [[filed-nowhere]]\n- [[wordy]]\n",
        encoding="utf-8",
      )
      (staging / "notes" / "wordy.md").write_text(
        "---\ntitle: Wordy\ntype: note\nmocs: [map]\ndescription: "
        + "x" * 2_001 + "\n---\nShort.\n",
        encoding="utf-8",
      )

      graph = runner.build_graph(staging, usage={})

      by_kind = {p["kind"]: p for p in graph["problems"]}
      self.assertEqual(by_kind["description_too_long"]["node"], "wordy")
      self.assertIn("bare_map_entry", by_kind)
      self.assertEqual(by_kind["bare_map_entry"]["node"], "filed-nowhere")
      self.assertIn("missing_description", by_kind)
      self.assertEqual(by_kind["missing_description"]["node"], "filed-nowhere")
      # Every reported problem is a warning, so none of them block publication
      # under the same predicate the runner uses.
      for problem in graph["problems"]:
        self.assertEqual(problem["severity"], "warning", problem)
      blocking = [
        p for p in graph["problems"] if p.get("severity") != "warning"
      ]
      self.assertEqual(blocking, [])

  def test_long_notes_and_wide_maps_are_not_rejected_by_magic_counts(self):
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

      self.assertEqual(problems, [])

  def test_one_bad_provenance_update_rejects_the_coherence_unit(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      staging = Path(raw) / "staging"
      _seed(staging)
      runner.build_graph(staging, usage={})
      valid = _proposal("c01")["updates"][0]
      invalid = {
        **_proposal("invented-source")["updates"][0],
        "path": "notes/unverified.md",
      }

      with self.assertRaises(runner.ProposalValidationError) as raised:
        runner._normalize_proposal(
          _reviewed({
            "updates": [valid, invalid], "deletes": [], "followups": [],
          }),
          allowed_chat_ids={"chat-1"},
          source_handles={"c01": "chat-1"},
        )

      self.assertEqual(raised.exception.code, "unverified_chat_provenance")
      self.assertEqual(raised.exception.path, "notes/unverified.md")






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
      runner._collect_capture_intake = lambda: runner.CaptureIntake([])
      runner._proposal = lambda *_args, **_kwargs: runner.ProposalOutcome(
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
      runner._collect_capture_intake = lambda: runner.CaptureIntake([])
      runner._proposal = lambda *_args, **_kwargs: runner.ProposalOutcome("ok", _reviewed({
        "summary": "replace the root", "followups": [], "deletes": [],
        "updates": [{"path": "index.md", "content": "# Empty root\n"}],
      }), "test", None, [])

      self.assertEqual(asyncio.run(runner.run()), 2)
      self.assertEqual(store.ready_pointer()["commit"], old["commit"])
      status = json.loads((store.STATE / "run-status.json").read_text())
      self.assertEqual(status["status"], "degraded")
      self.assertEqual(status["reason"], "topology_regression")

  def test_proposal_data_exposes_short_handles_not_canonical_chat_ids(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      staging = Path(raw) / "staging"
      staging.mkdir()
      canonical = "1f905105-a3a6-4a67-a6e3-1b34ea6963d8"

      data = json.loads(runner._proposal_data(staging, [{
        "id": canonical, "title": "Capability work", "messages": [],
      }]))

      row = data["captured_chats"][0]
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
      runner._collect_capture_intake = lambda: runner.CaptureIntake([{"id": canonical, "messages": []}])
      proposal = runner._normalize_proposal(
        _proposal("c01"),
        allowed_chat_ids={canonical},
        source_handles={"c01": canonical},
      )
      runner._proposal = lambda *_args, **_kwargs: runner.ProposalOutcome(
        "ok", proposal, "test", None, [],
      )

      self.assertEqual(asyncio.run(runner.run()), 0)

      note = store.read_revision_file(
        store.ready_pointer()["commit"], "notes/quiet-ui.md",
      )
      self.assertIn(f"source: [chat:{canonical}]", note)
      self.assertNotIn("chat:c01", note)
      self.assertEqual(store.load_captures(), [])

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
      runner._text_proposal = lambda choice, _prompt, **_kwargs: runner.AnalystResult(
        (
          _reviewed({
            "summary": "mixed primary",
            "followups": [],
            "deletes": [],
            "updates": [
              _proposal("c01")["updates"][0],
              {
                **_proposal("invented-source")["updates"][0],
                "path": "notes/unverified.md",
              },
            ],
          })
          if choice["provider"] == "claude"
          else _proposal("c01")
        ),
      )

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
      runner._text_proposal = lambda *_args, **_kwargs: runner.AnalystResult(
        _proposal("invented-source"),
      )

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

      data = json.loads(runner._proposal_data(staging, [{
        "id": "chat-one",
        "title": "Align the old prior fact",
        "messages": [{"role": "user", "text": "The durable old detail changed."}],
      }], extra_note_paths={"notes/old.md"}))
      old_row = next(
        row for row in data["existing_note_contents"]
        if row["path"] == "notes/old.md"
      )
      self.assertIn("durable old detail", old_row["content"])
      changed, deleted = runner._apply_proposal(
        staging,
        _reviewed({
          "updates": [{"path": "notes/old.md", "content": old.replace("detail", "detail, aligned")}],
          "deletes": [],
        }),
        allowed_chat_ids=runner._known_chat_sources(staging),
      )
      self.assertEqual((changed, deleted), (["notes/old.md"], []))

  def test_prompt_supplies_semantic_identities_and_only_requested_complete_notes(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      staging = Path(raw) / "staging"
      _seed(staging)
      route = "# Topic\n" + ("complete routing text\n" * 260)
      note = (
        "---\ntitle: Large optional detail\ntype: note\n"
        "description: The complete large-note detail\n---\n"
        + ("optional detail\n" * 400)
      )
      (staging / "mocs" / "topic.md").write_text(route, encoding="utf-8")
      (staging / "notes" / "large.md").write_text(note, encoding="utf-8")
      runner.build_graph(staging, usage={})

      data = json.loads(runner._proposal_data(staging, [{
        "id": "chat-one",
        "title": "Large note detail",
        "messages": [{"role": "user", "text": "The large optional detail changed."}],
      }], extra_note_paths={"notes/large.md"}))
      route_row = next(
        row for row in data["existing_graph"]["mocs"]
        if row[0] == "mocs/topic.md"
      )
      note_row = next(
        row for row in data["existing_graph"]["notes"]
        if row[0] == "notes/large.md"
      )

      self.assertEqual(route_row[0], "mocs/topic.md")
      self.assertEqual(note_row, ["notes/large.md", "Large optional detail"])
      self.assertNotIn(
        "complete routing text", json.dumps(data["existing_graph"]),
      )
      related = next(
        row for row in data["existing_note_contents"]
        if row["path"] == "notes/large.md"
      )
      self.assertEqual(related["content"], note)

  def test_validated_delete_can_merge_duplicate_without_touching_index(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      staging = Path(raw) / "staging"
      _seed(staging)
      duplicate = staging / "notes" / "duplicate.md"
      duplicate.write_text("old duplicate", encoding="utf-8")

      changed, deleted = runner._apply_proposal(
        staging, _reviewed({"updates": [], "deletes": ["notes/duplicate.md"]}),
        allowed_chat_ids=set(),
      )
      self.assertEqual((changed, deleted), ([], ["notes/duplicate.md"]))
      self.assertFalse(duplicate.exists())
      with self.assertRaisesRegex(ValueError, "deletion"):
        runner._apply_proposal(
          staging, _reviewed({"updates": [], "deletes": ["index.md"]}),
          allowed_chat_ids=set(),
        )
      with self.assertRaisesRegex(ValueError, "deletion"):
        runner._apply_proposal(
          staging, _reviewed({"updates": [], "deletes": ["mocs/memory-unfiled.md"]}),
          allowed_chat_ids=set(),
        )
      with self.assertRaisesRegex(ValueError, "deletion"):
        runner._apply_proposal(
          staging,
          _reviewed({"updates": [], "deletes": ["mocs/maintaining-memory.md"]}),
          allowed_chat_ids=set(),
        )

  def test_a_batch_routing_to_an_uncreated_note_is_rejected_and_rolled_back(self):
    """One dangling routing edge must cost its batch, never the whole run.

    A batch whose routing text references a note it does not create is rejected
    and rolled back, while a batch that creates the note it routes to in the
    same proposal is still accepted - otherwise the ordinary "record a fact,
    then file it" flow would break.
    """
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      staging = Path(raw) / "staging"
      _seed(staging)
      baseline = runner.build_graph(staging, usage={})
      moc = staging / "mocs" / "about-the-user.md"
      before = moc.read_text(encoding="utf-8")

      dangling = (
        "---\ntitle: About the user\ntype: moc\n---\n# About\n\n"
        "- [[a-preference-this-batch-never-writes]]\n"
      )
      with self.assertRaises(runner.ProposalValidationError) as caught:
        runner._apply_validated_proposal(
          staging,
          {"updates": [{"path": "mocs/about-the-user.md", "content": dangling}],
           "deletes": []},
          baseline=baseline,
        )
      self.assertEqual(caught.exception.code, "invalid_graph")
      self.assertIn("dangling_link", str(caught.exception))
      self.assertEqual(moc.read_text(encoding="utf-8"), before)

      note = (
        "---\ntype: note\ntitle: A preference\ndescription: a preference\n"
        "---\nThe durable preference detail.\n"
      )
      _normalized, changed, deleted, _graph = runner._apply_validated_proposal(
        staging,
        {"updates": [
          {"path": "notes/a-preference-this-batch-never-writes.md", "content": note},
          {"path": "mocs/about-the-user.md", "content": dangling},
        ], "deletes": []},
        baseline=baseline,
      )
      self.assertEqual(deleted, [])
      self.assertIn("mocs/about-the-user.md", changed)
      self.assertEqual(moc.read_text(encoding="utf-8"), dangling)

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
      runner._collect_capture_intake = lambda: runner.CaptureIntake([])
      runner._proposal = lambda *_args, **_kwargs: runner.ProposalOutcome("ok", _reviewed({
        "summary": "no provider", "followups": [], "updates": [], "deletes": [],
      }), "test", None, [])

      self.assertEqual(asyncio.run(runner.run()), 1)
      self.assertIsNone(store.ready_pointer())
      self.assertFalse((store.REPOSITORY / ".git" / "index.lock").exists())

  def test_liveness_accepts_authority_schema_rollout(self):
    with tempfile.TemporaryDirectory() as raw:
      _store, runner = _load(Path(raw))
      contract = {
        "schema": 3,
        "data": {"shared_memory": "write"},
        "background": {
          "job": "fetch.sh", "mode": "scheduled", "authority": "scoped",
        },
      }
      runner._api_json = lambda _path: {
        "id": 7,
        "slug": "memory-2",
        "capability_contract": contract,
      }

      self.assertTrue(runner._app_active(7))
      self.assertFalse(runner._app_active(8))
      contract["schema"] = 4
      contract["background"].pop("authority")
      self.assertTrue(runner._app_active(7))
      contract["schema"] = 2
      self.assertFalse(runner._app_active(7))

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

  def test_app_agent_override_ignores_historical_effort(self):
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
        {"provider": "codex", "model": "same", "effort": None},
        {"provider": "codex", "model": "same", "effort": "high"},
      ])

      settings["effort"] = "medium"
      path.write_text(json.dumps(settings))
      self.assertEqual(runner._agent_choices(7), [
        {"provider": "codex", "model": "same", "effort": None},
        {"provider": "codex", "model": "same", "effort": "high"},
      ])


if __name__ == "__main__":
  unittest.main()
