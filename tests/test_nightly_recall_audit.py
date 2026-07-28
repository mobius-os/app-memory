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
    self.assertEqual(stats["selection_misses"], 0)
    self.assertEqual(stats["overreaches"], 1)
    self.assertEqual(stats["overreach_rate"], 1.0)
    self.assertEqual(stats["no_memory_rate"], 0.0)
    self.assertEqual(stats["model_to_host_selection_override_rate"], 0.0)
    self.assertEqual(stats["graph_nodes"], 3)
    self.assertEqual(stats["live_policy"], {"breadth": 4, "depth": 4})
    self.assertEqual(stats["night_policy"], {"breadth": 6, "depth": 6})

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
