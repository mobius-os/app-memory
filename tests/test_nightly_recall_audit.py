from __future__ import annotations

import json

import pytest

import memory_runner


def test_nightly_policy_defaults_to_six_by_six(monkeypatch):
  monkeypatch.setattr(memory_runner, "_settings", lambda _app_id: {})
  assert memory_runner._night_policy(57) == (6, 6)


def test_nightly_prompt_requires_learn_recall_repair_and_prune(tmp_path):
  (tmp_path / "graph.json").write_text(json.dumps({
    "nodes": [], "problems": [],
  }))
  audit = {
    "read_id": "read-1",
    "question": "What matters?",
    "live": {"selected": []},
    "deep": {"selected": ["notes/answer.md"], "opened": []},
    "potential_misses": ["notes/answer.md"],
  }
  prompt = memory_runner._proposal_prompt(tmp_path, [], [audit])
  assert "Learn." in prompt
  assert "Review EVERY `read_audits` entry" in prompt
  assert "repair the shortest useful route" in prompt
  assert "stale, superseded, or obsolete" in prompt
  assert '"read_id": "read-1"' in prompt


def test_audit_verdicts_must_cover_each_replayed_read_exactly_once():
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
  with pytest.raises(memory_runner.ProposalValidationError) as raised:
    memory_runner._normalize_audit_verdicts(proposal, audits)
  assert raised.value.code == "incomplete_read_audits"


def test_read_cursor_advances_only_through_recorded_success(monkeypatch, tmp_path):
  state = tmp_path / "app-state"
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
  monkeypatch.setattr(memory_runner, "STATE", state)
  monkeypatch.setattr(memory_runner, "_RECALL_STATS", stats_path)

  assert [item["read_id"] for item in memory_runner._pending_read_traces()] == ["new"]


def test_recall_stats_split_route_miss_overreach_and_graph_scale(monkeypatch, tmp_path):
  state = tmp_path / "app-state"
  monkeypatch.setattr(memory_runner, "STATE", state)
  monkeypatch.setattr(memory_runner, "_RECALL_STATS", state / "recall-stats.json")
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

  memory_runner._record_recall_audits(
    "run-1",
    audits,
    proposal,
    graph,
    live_policy=(4, 4),
    night_policy=(6, 6),
  )

  stats = json.loads((state / "recall-stats.json").read_text())
  assert stats["reads_audited"] == 1
  assert stats["misses"] == 1
  assert stats["miss_rate"] == 1.0
  assert stats["route_misses"] == 1
  assert stats["selection_misses"] == 0
  assert stats["overreaches"] == 1
  assert stats["overreach_rate"] == 1.0
  assert stats["no_memory_rate"] == 0.0
  assert stats["model_to_host_selection_override_rate"] == 0.0
  assert stats["graph_nodes"] == 3
  assert stats["live_policy"] == {"breadth": 4, "depth": 4}
  assert stats["night_policy"] == {"breadth": 6, "depth": 6}


def test_audit_verdict_requires_nodes_for_miss_and_overreach():
  audits = [{"read_id": "one"}]
  proposal = {"read_audits": [{
    "read_id": "one",
    "outcome": "miss",
    "overreach": True,
    "missed_nodes": [],
    "overselected_nodes": [],
    "reason": "Unsupported classification.",
  }]}
  with pytest.raises(memory_runner.ProposalValidationError) as raised:
    memory_runner._normalize_audit_verdicts(proposal, audits)
  assert raised.value.code == "invalid_read_audits"


def test_host_selection_override_detects_model_empty_replaced_by_host():
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
  assert memory_runner._host_selection_override(
    traversal, ["notes/near.md"],
  ) is True
  assert memory_runner._host_selection_override(traversal, []) is False
