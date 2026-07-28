from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import memory_runner


class NightlyRecallAuditTests(unittest.TestCase):
  def test_nightly_policy_defaults_to_six_by_six(self):
    with mock.patch.object(memory_runner, "_settings", return_value={}):
      self.assertEqual(memory_runner._night_policy(57), (6, 6))

  def test_nightly_prompt_requires_learn_recall_repair_and_prune(self):
    with tempfile.TemporaryDirectory() as raw:
      root = Path(raw)
      (root / "graph.json").write_text(json.dumps({
        "nodes": [], "problems": [],
      }))
      audit = {
        "read_id": "read-1",
        "question": "What matters?",
        "live": {"selected": []},
        "deep": {"selected": ["notes/answer.md"], "opened": []},
        "potential_misses": ["notes/answer.md"],
      }
      prompt = memory_runner._proposal_prompt(root, [], [audit])

    self.assertIn("Learn.", prompt)
    self.assertIn("Review EVERY `read_audits` entry", prompt)
    self.assertIn("repair the shortest useful route", prompt)
    self.assertIn("stale, superseded, or obsolete", prompt)
    self.assertIn('"read_id": "read-1"', prompt)

  def test_audit_verdicts_must_cover_each_replayed_read_exactly_once(self):
    audits = [{"read_id": "one"}, {"read_id": "two"}]
    proposal = {
      "read_audits": [
        {
          "read_id": "one",
          "outcome": "ok",
          "overreach": False,
          "missed_nodes": [],
          "overselected_nodes": [],
          "reason": "Live recall was sufficient.",
        },
      ],
    }
    with self.assertRaises(memory_runner.ProposalValidationError) as raised:
      memory_runner._normalize_audit_verdicts(proposal, audits)
    self.assertEqual(raised.exception.code, "incomplete_read_audits")

  def test_read_cursor_advances_only_through_recorded_success(self):
    with tempfile.TemporaryDirectory() as raw:
      state = Path(raw) / "app-state"
      log = state / "read-log" / "2026-07-28.jsonl"
      log.parent.mkdir(parents=True)
      records = [
        {
          "schema": 3,
          "read_id": "old",
          "at": "2026-07-28T00:00:00+00:00",
          "question": "old question",
        },
        {
          "schema": 3,
          "read_id": "new",
          "at": "2026-07-28T01:00:00+00:00",
          "question": "new question",
        },
      ]
      log.write_text("\n".join(json.dumps(item) for item in records) + "\n")
      stats_path = state / "recall-stats.json"
      stats_path.write_text(json.dumps({
        "last_audited_at": "2026-07-28T00:30:00+00:00",
      }))

      with (
        mock.patch.object(memory_runner, "STATE", state),
        mock.patch.object(memory_runner, "_RECALL_STATS", stats_path),
      ):
        pending = memory_runner._pending_read_traces()

    self.assertEqual([item["read_id"] for item in pending], ["new"])

  def test_recall_stats_split_route_miss_overreach_and_graph_scale(self):
    with tempfile.TemporaryDirectory() as raw:
      state = Path(raw) / "app-state"
      audits = [{
        "read_id": "read-1",
        "at": "2026-07-28T01:00:00+00:00",
        "question": "What matters?",
        "live": {
          "selected": ["notes/adjacent.md"],
          "opened": [{"path": "index.md"}, {"path": "mocs/topic.md"}],
          "frontier_at_stop": [],
          "stop_reason": "navigator_finished",
          "host_selection_override": False,
        },
        "deep": {
          "selected": ["notes/answer.md"],
          "stop_reason": "navigator_finished",
        },
        "potential_misses": ["notes/answer.md"],
      }]
      proposal = {"read_audits": [{
        "read_id": "read-1",
        "outcome": "miss",
        "overreach": True,
        "missed_nodes": ["notes/answer.md"],
        "overselected_nodes": ["notes/adjacent.md"],
        "reason": "The useful branch was hidden below the live depth.",
      }]}
      graph = {"nodes": [{}, {}, {}], "edges": [{}, {}]}

      with (
        mock.patch.object(memory_runner, "STATE", state),
        mock.patch.object(
          memory_runner,
          "_RECALL_STATS",
          state / "recall-stats.json",
        ),
      ):
        memory_runner._record_recall_audits(
          "run-1",
          audits,
          proposal,
          graph,
          live_policy=(4, 4),
          night_policy=(6, 6),
        )
        stats = json.loads((state / "recall-stats.json").read_text())

    self.assertEqual(stats["reads_audited"], 1)
    self.assertEqual(stats["misses"], 1)
    self.assertEqual(stats["miss_rate"], 1.0)
    self.assertEqual(stats["route_misses"], 1)
    self.assertEqual(stats["continuation_misses"], 0)
    self.assertEqual(stats["selection_misses"], 0)
    self.assertEqual(stats["overreaches"], 1)
    self.assertEqual(stats["overreach_rate"], 1.0)
    self.assertEqual(stats["no_memory_rate"], 0.0)
    self.assertEqual(stats["model_to_host_selection_override_rate"], 0.0)
    self.assertEqual(stats["graph_nodes"], 3)
    self.assertEqual(stats["live_policy"], {"breadth": 4, "depth": 4})
    self.assertEqual(stats["night_policy"], {"breadth": 6, "depth": 6})

  def test_recall_stats_distinguish_unopened_frontier_from_route_miss(self):
    with tempfile.TemporaryDirectory() as raw:
      state = Path(raw) / "app-state"
      audits = [{
        "read_id": "read-1",
        "at": "2026-07-28T01:00:00+00:00",
        "question": "What matters?",
        "live": {
          "selected": [],
          "opened": [{"path": "index.md"}, {"path": "mocs/topic.md"}],
          "frontier_at_stop": [{
            "from": "topic",
            "depth": 1,
            "nodes": [{"id": "answer", "path": "notes/answer.md"}],
          }],
          "stop_reason": "navigator_finished",
          "host_selection_override": False,
        },
        "deep": {
          "selected": ["notes/answer.md"],
          "stop_reason": "navigator_finished",
        },
        "potential_misses": ["notes/answer.md"],
      }]
      proposal = {"read_audits": [{
        "read_id": "read-1",
        "outcome": "miss",
        "overreach": False,
        "missed_nodes": ["notes/answer.md"],
        "overselected_nodes": [],
        "reason": "The live reader stopped before opening one relevant child.",
      }]}

      with (
        mock.patch.object(memory_runner, "STATE", state),
        mock.patch.object(
          memory_runner,
          "_RECALL_STATS",
          state / "recall-stats.json",
        ),
      ):
        memory_runner._record_recall_audits(
          "run-1",
          audits,
          proposal,
          {"nodes": [], "edges": []},
          live_policy=(4, 4),
          night_policy=(6, 6),
        )
        stats = json.loads((state / "recall-stats.json").read_text())

    self.assertEqual(stats["continuation_misses"], 1)
    self.assertEqual(stats["route_misses"], 0)
    self.assertEqual(stats["selection_misses"], 0)
    self.assertEqual(stats["recent"][0]["miss_class"], "continuation")

  def test_prompt_budget_trims_graph_before_dropping_chat(self):
    chats = [{
      "id": "chat-one",
      "title": "Useful chat",
      "messages": [{"role": "user", "text": "A durable fact"}],
    }]
    with tempfile.TemporaryDirectory() as raw:
      with (
        mock.patch.object(memory_runner, "_MAX_PROMPT_DATA_CHARS", 900),
        mock.patch.object(memory_runner, "_maintenance_flags", return_value=[]),
        mock.patch.object(memory_runner, "_graph_catalog", return_value=[
          {"id": f"node-{index}", "content": "x" * 250}
          for index in range(5)
        ]),
      ):
        encoded, included = memory_runner._proposal_envelope(
          Path(raw), chats, [],
        )
    payload = json.loads(encoded)

    self.assertEqual(included, chats)
    self.assertEqual(
      payload["redacted_recent_chats"][0]["source_handle"],
      "chat:c01",
    )
    self.assertLess(len(payload["existing_graph"]), 5)
    self.assertLessEqual(len(encoded), 900)

  def test_prompt_budget_skips_oversized_chat_and_keeps_trying(self):
    chats = [
      {
        "id": "too-large",
        "title": "Large",
        "messages": [{"role": "user", "text": "x" * 1000}],
      },
      {
        "id": "small",
        "title": "Small",
        "messages": [{"role": "user", "text": "fits"}],
      },
    ]
    with tempfile.TemporaryDirectory() as raw:
      with (
        mock.patch.object(memory_runner, "_MAX_PROMPT_DATA_CHARS", 500),
        mock.patch.object(memory_runner, "_maintenance_flags", return_value=[]),
        mock.patch.object(memory_runner, "_graph_catalog", return_value=[]),
      ):
        encoded, included = memory_runner._proposal_envelope(
          Path(raw), chats, [],
        )
    payload = json.loads(encoded)

    self.assertEqual(included, [chats[1]])
    self.assertEqual(
      [
        item["source_handle"]
        for item in payload["redacted_recent_chats"]
      ],
      ["chat:c02"],
    )

  def test_audit_verdict_requires_nodes_for_miss_and_overreach(self):
    audits = [{"read_id": "one"}]
    proposal = {"read_audits": [{
      "read_id": "one",
      "outcome": "miss",
      "overreach": True,
      "missed_nodes": [],
      "overselected_nodes": [],
      "reason": "Unsupported classification.",
    }]}
    with self.assertRaises(memory_runner.ProposalValidationError) as raised:
      memory_runner._normalize_audit_verdicts(proposal, audits)
    self.assertEqual(raised.exception.code, "invalid_read_audits")

  def test_host_selection_override_detects_model_empty_replaced_by_host(self):
    traversal = {
      "opened": [
        {"id": "index", "path": "index.md"},
        {"id": "near", "path": "notes/near.md"},
      ],
      "decisions": [{
        "source": "model",
        "selected": [],
      }],
    }
    self.assertTrue(memory_runner._host_selection_override(
      traversal, ["notes/near.md"],
    ))
    self.assertFalse(memory_runner._host_selection_override(traversal, []))


if __name__ == "__main__":
  unittest.main()
