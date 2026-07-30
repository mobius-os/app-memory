from __future__ import annotations

import asyncio
import json

import pytest

import memory_runner


def _self_review():
  return {
    "hardest_decision": "Distinguishing durable facts from transient context.",
    "possibly_missed": "none",
    "prompt_change": "none",
  }


def _scoped_contract():
  return {
    "schema": 3,
    "data": {"shared_memory": "write"},
    "background": {
      "job": "fetch.sh",
      "mode": "scheduled",
      "authority": "scoped",
    },
  }


def test_app_active_requires_current_scoped_authority_receipt(monkeypatch):
  app = {
    "id": 57,
    "system_app": True,
    "capability_contract": _scoped_contract(),
  }
  monkeypatch.setattr(memory_runner, "_api_json", lambda _path: app)
  assert memory_runner._app_active(57) is True

  legacy = {
    **app,
    "capability_contract": {
      **_scoped_contract(),
      "schema": 2,
      "background": {
        "job": "fetch.sh",
        "mode": "scheduled",
        "agent": True,
        "authority": "scoped_system_job",
      },
    },
  }
  monkeypatch.setattr(memory_runner, "_api_json", lambda _path: legacy)
  assert memory_runner._app_active(57) is False

  contradictory = {
    **app,
    "capability_contract": {
      **_scoped_contract(),
      "background": {
        **_scoped_contract()["background"],
        "agent": True,
      },
    },
  }
  monkeypatch.setattr(memory_runner, "_api_json", lambda _path: contradictory)
  assert memory_runner._app_active(57) is False


def test_preflight_failure_replaces_stale_run_status(monkeypatch):
  recorded = []
  monkeypatch.setattr(memory_runner, "_app_id", lambda: 57)
  monkeypatch.setattr(memory_runner, "APP_TOKEN", "")
  monkeypatch.setattr(
    memory_runner, "ready_pointer", lambda: {"commit": "ready-commit"},
  )
  monkeypatch.setattr(
    memory_runner, "_record_run_status", lambda record: recorded.append(record),
  )

  assert asyncio.run(memory_runner.run()) == 1
  assert len(recorded) == 1
  assert recorded[0]["status"] == "failed"
  assert recorded[0]["error_code"] == "missing_scoped_token"
  assert recorded[0]["commit"] == "ready-commit"
  assert recorded[0]["run_id"].startswith("preflight-")


def test_read_audit_batch_is_oldest_first_and_bounded():
  records = [{"read_id": str(index)} for index in range(30)]

  batch, deferred = memory_runner._read_audit_batch(records)

  assert batch == records[:memory_runner._MAX_READ_AUDITS_PER_RUN]
  assert deferred == 6


def test_audit_prompt_batch_defers_newer_replays_before_dropping_routes(
  monkeypatch, tmp_path,
):
  audits = [{"read_id": str(index)} for index in range(4)]

  def envelope(_staging, _chats, selected):
    if len(selected) > 2:
      raise memory_runner.ProposalValidationError(
        "routing_context_over_budget", "required routes no longer fit",
      )
    return "{}", []

  monkeypatch.setattr(memory_runner, "_proposal_envelope", envelope)

  batch, deferred = memory_runner._audit_prompt_batch(tmp_path, audits)

  assert batch == audits[:2]
  assert deferred == 2


def test_combined_proposal_preserves_each_batch_report():
  combined = memory_runner._combined_proposal([
    {
      "summary": "  First   batch. ",
      "followups": ["Check this", "Shared"],
      "read_audits": [{"read_id": "one"}],
      "self_review": _self_review(),
    },
    {
      "summary": "Second batch.",
      "followups": ["Shared", "Check that"],
      "read_audits": [{"read_id": "two"}],
      "self_review": _self_review(),
    },
  ])

  assert combined == {
    "summary": "First batch. Second batch.",
    "followups": ["Check this", "Shared", "Check that"],
    "read_audits": [{"read_id": "one"}, {"read_id": "two"}],
    "writer_self_reviews": [_self_review(), _self_review()],
  }


def test_writer_self_review_is_required_and_normalized():
  proposal = {
    "updates": [], "deletes": [], "followups": [], "read_audits": [],
  }
  with pytest.raises(memory_runner.ProposalValidationError) as raised:
    memory_runner._normalize_proposal(
      proposal, allowed_chat_ids=set(), source_handles={},
    )
  assert raised.value.code == "invalid_self_review"

  proposal["self_review"] = {
    "hardest_decision": "  Pick   the durable route. ",
    "possibly_missed": " none ",
    "prompt_change": " none ",
  }
  normalized = memory_runner._normalize_proposal(
    proposal, allowed_chat_ids=set(), source_handles={},
  )
  assert normalized["self_review"] == {
    "hardest_decision": "Pick the durable route.",
    "possibly_missed": "none",
    "prompt_change": "none",
  }


def test_terminal_provider_failure_is_skipped_for_later_proposal_batches(
  monkeypatch, tmp_path,
):
  calls = []
  providers = memory_runner.ProviderPool([
    {"provider": "claude", "model": "claude-test", "effort": None},
    {"provider": "codex", "model": "gpt-test", "effort": None},
  ])
  proposal = {
    "updates": [],
    "deletes": [],
    "summary": "No durable changes.",
    "followups": [],
    "read_audits": [],
    "self_review": _self_review(),
  }
  monkeypatch.setattr(memory_runner, "_proposal_prompt", lambda *_args: "prompt")
  monkeypatch.setattr(memory_runner, "_known_chat_sources", lambda _path: set())
  monkeypatch.setattr(
    memory_runner,
    "_claude_proposal",
    lambda *_args: (
      calls.append("claude")
      or memory_runner.AnalystResult(
        None,
        memory_runner.ProviderFailure("usage_limit", True, "provider"),
      )
    ),
  )
  monkeypatch.setattr(
    memory_runner,
    "_codex_proposal",
    lambda *_args: (
      calls.append("codex") or memory_runner.AnalystResult(proposal)
    ),
  )

  first = memory_runner._proposal(57, tmp_path, [], [], providers)
  second = memory_runner._proposal(57, tmp_path, [], [], providers)

  assert first.status == second.status == "ok"
  assert calls == ["claude", "codex", "codex"]
  assert second.attempted_agents[0]["skipped_reason"] == "usage_limit"


def test_transient_provider_failure_is_retried_on_the_next_batch(
  monkeypatch, tmp_path,
):
  calls = []
  providers = memory_runner.ProviderPool([
    {"provider": "claude", "model": "claude-test", "effort": None},
    {"provider": "codex", "model": "gpt-test", "effort": None},
  ])
  proposal = {
    "updates": [], "deletes": [], "summary": "No durable changes.",
    "followups": [], "read_audits": [],
    "self_review": _self_review(),
  }
  monkeypatch.setattr(memory_runner, "_proposal_prompt", lambda *_args: "prompt")
  monkeypatch.setattr(memory_runner, "_known_chat_sources", lambda _path: set())
  monkeypatch.setattr(
    memory_runner,
    "_claude_proposal",
    lambda *_args: (
      calls.append("claude")
      or memory_runner.AnalystResult(
        None, memory_runner.ProviderFailure("timeout"),
      )
    ),
  )
  monkeypatch.setattr(
    memory_runner,
    "_codex_proposal",
    lambda *_args: (
      calls.append("codex") or memory_runner.AnalystResult(proposal)
    ),
  )

  memory_runner._proposal(57, tmp_path, [], [], providers)
  memory_runner._proposal(57, tmp_path, [], [], providers)

  assert calls == ["claude", "codex", "claude", "codex"]


def test_batch_coordinator_combines_terminal_fallback_and_topology_rollback(
  monkeypatch, tmp_path,
):
  chats = [{"id": f"chat-{index}"} for index in range(5)]
  providers = memory_runner.ProviderPool([
    {"provider": "claude", "model": "claude-test", "effort": None},
    {"provider": "codex", "model": "gpt-test", "effort": None},
  ])
  calls = []
  apply_count = 0
  graph = {"nodes": [], "edges": [], "problems": []}
  proposal = {
    "updates": [], "deletes": [], "summary": "Processed a batch.",
    "followups": [], "read_audits": [],
    "self_review": _self_review(),
  }
  monkeypatch.setattr(memory_runner, "_proposal_prompt", lambda *_args: "prompt")
  monkeypatch.setattr(memory_runner, "_known_chat_sources", lambda _path: set())
  monkeypatch.setattr(
    memory_runner,
    "_proposal_batch",
    lambda _staging, remaining, _audits: remaining[:2],
  )
  monkeypatch.setattr(
    memory_runner,
    "_claude_proposal",
    lambda *_args: (
      calls.append("claude")
      or memory_runner.AnalystResult(
        None,
        memory_runner.ProviderFailure("usage_limit", True, "provider"),
      )
    ),
  )
  monkeypatch.setattr(
    memory_runner,
    "_codex_proposal",
    lambda *_args: (
      calls.append("codex") or memory_runner.AnalystResult(proposal)
    ),
  )

  def apply(_staging, value, **_kwargs):
    nonlocal apply_count
    apply_count += 1
    if apply_count == 3:
      raise memory_runner.ProposalValidationError(
        "topology_regression", "specific routing would regress",
      )
    return value, [], [], graph

  monkeypatch.setattr(memory_runner, "_apply_validated_proposal", apply)

  result = memory_runner._consolidate_batches(
    57, tmp_path, graph, chats, [], providers,
  )

  assert calls == ["claude", "codex", "codex", "codex"]
  assert [chat["id"] for chat in result.accepted_chats] == [
    "chat-0", "chat-1", "chat-2", "chat-3",
  ]
  assert [chat["id"] for chat in result.remaining_chats] == ["chat-4"]
  assert len(result.proposals) == 2
  assert result.deferred_reason == "topology_regression"
  assert result.deferred_attempts[-1]["rejection_code"] == "topology_regression"


@pytest.mark.parametrize(
  ("message", "code", "scope"),
  [
    ("Monthly usage limit reached", "usage_limit", "provider"),
    ("Authentication failed: please login", "authentication", "provider"),
    ("Unknown model claude-future", "model_unavailable", "choice"),
  ],
)
def test_terminal_provider_errors_have_typed_scope(message, code, scope):
  failure = memory_runner.classify_process_failure(1, stderr=message)

  assert failure.code == code
  assert failure.terminal is True
  assert failure.scope == scope


def test_rate_limit_is_not_cached_as_a_terminal_failure():
  failure = memory_runner.classify_process_failure(
    1, stderr="Temporary rate limit; retry later",
  )

  assert failure.code == "process_exit_1"
  assert failure.terminal is False


def test_rejected_batch_restores_files_and_derived_graph(monkeypatch, tmp_path):
  mocs = tmp_path / "mocs"
  mocs.mkdir()
  topic = mocs / "topic.md"
  original = "# Topic\n\n- [[kept-note]]\n"
  topic.write_text(original)
  baseline = {
    "nodes": [
      {"id": "index"},
      {"id": "topic"},
      {"id": "kept-note"},
    ],
    "edges": [
      {"source": "index", "target": "topic"},
      {"source": "topic", "target": "kept-note"},
    ],
    "problems": [],
  }
  regressed = {
    **baseline,
    "edges": [{"source": "index", "target": "topic"}],
  }
  builds = [regressed, baseline]
  monkeypatch.setattr(
    memory_runner,
    "build_graph",
    lambda *_args, **_kwargs: builds.pop(0),
  )
  proposal = {
    "updates": [{"path": "mocs/topic.md", "content": "# Topic\n"}],
    "deletes": [],
    "followups": [],
    "self_review": _self_review(),
  }

  with pytest.raises(memory_runner.ProposalValidationError) as raised:
    memory_runner._apply_validated_proposal(
      tmp_path,
      proposal,
      allowed_chat_ids=set(),
      source_handles={},
      baseline=baseline,
    )

  assert raised.value.code == "topology_regression"
  assert topic.read_text() == original
  assert builds == []


def test_run_reaches_consolidation_with_a_bounded_recall_audit_batch(
  monkeypatch, tmp_path,
):
  traces = [{"read_id": str(index)} for index in range(30)]
  audited = []
  statuses = []
  graph = {"nodes": [], "edges": [], "problems": []}

  monkeypatch.setattr(memory_runner, "_app_id", lambda: 57)
  monkeypatch.setattr(memory_runner, "APP_TOKEN", "scoped-token")
  monkeypatch.setattr(memory_runner, "_app_active", lambda _app_id: True)
  monkeypatch.setattr(
    memory_runner, "ready_pointer", lambda: {"commit": "ready"},
  )
  monkeypatch.setattr(
    memory_runner, "start_staging", lambda _seed: ("run-1", tmp_path),
  )
  monkeypatch.setattr(memory_runner, "discard_staging", lambda _path: None)
  monkeypatch.setattr(memory_runner, "build_graph", lambda *_args, **_kwargs: graph)
  monkeypatch.setattr(
    memory_runner, "_reconcile_app_owned_docs", lambda *_args: ([], []),
  )
  monkeypatch.setattr(memory_runner, "_redacted_chats", lambda: [])
  monkeypatch.setattr(memory_runner, "_remember_pending_chats", lambda _chats: None)
  monkeypatch.setattr(memory_runner, "_pending_read_traces", lambda: traces)

  def audit(_app_id, _commit, selected, _staging=None):
    audited.extend(selected)
    return selected

  monkeypatch.setattr(memory_runner, "_audit_reads", audit)
  monkeypatch.setattr(
    memory_runner,
    "_audit_prompt_batch",
    lambda _staging, remaining: (
      remaining[:5],
      max(0, len(remaining) - 5),
    ),
  )
  monkeypatch.setattr(
    memory_runner,
    "_proposal",
    lambda _app_id, _staging, _chats, audits, _providers: memory_runner.ProposalOutcome(
      "ok",
      {
        "updates": [],
        "deletes": [],
        "summary": f"Audited {len(audits)} reads.",
        "followups": [],
        "read_audits": [],
        "self_review": _self_review(),
      },
      "codex",
      "gpt-test",
      [],
    ),
  )
  monkeypatch.setattr(memory_runner, "_known_chat_sources", lambda _path: set())
  monkeypatch.setattr(memory_runner, "_repair_orphans", lambda *_args: [])
  monkeypatch.setattr(
    memory_runner,
    "publish",
    lambda _staging: {"commit": "next", "changed": True},
  )
  monkeypatch.setattr(memory_runner, "_acknowledge_pending_chats", lambda _items: None)
  monkeypatch.setattr(
    memory_runner, "_record_run_status", lambda status: statuses.append(status),
  )
  monkeypatch.setattr(memory_runner, "_append_update_log", lambda *_args: None)
  monkeypatch.setattr(
    memory_runner,
    "_record_recall_audits",
    lambda *_args, **_kwargs: None,
  )
  monkeypatch.setattr(memory_runner, "_live_policy", lambda _app_id: (4, 4))
  monkeypatch.setattr(memory_runner, "_night_policy", lambda _app_id: (6, 6))

  assert asyncio.run(memory_runner.run()) == 0
  assert audited == traces[:memory_runner._MAX_READ_AUDITS_PER_RUN]
  assert statuses[-1]["read_audit_count"] == 24
  assert statuses[-1]["deferred_read_audit_count"] == 6
  assert statuses[-1]["audit_proposal_batch_count"] == 5
  assert statuses[-1]["chat_proposal_batch_count"] == 0


def test_run_consolidates_multiple_bounded_chat_batches_before_one_publish(
  monkeypatch, tmp_path,
):
  chats = [{"id": f"chat-{index}"} for index in range(5)]
  proposed_batches = []
  acknowledged = []
  published = []
  statuses = []
  graph = {"nodes": [], "edges": [], "problems": []}

  monkeypatch.setattr(memory_runner, "_app_id", lambda: 57)
  monkeypatch.setattr(memory_runner, "APP_TOKEN", "scoped-token")
  monkeypatch.setattr(memory_runner, "_app_active", lambda _app_id: True)
  monkeypatch.setattr(
    memory_runner, "ready_pointer", lambda: {"commit": "ready"},
  )
  monkeypatch.setattr(
    memory_runner, "start_staging", lambda _seed: ("run-1", tmp_path),
  )
  monkeypatch.setattr(memory_runner, "discard_staging", lambda _path: None)
  monkeypatch.setattr(memory_runner, "build_graph", lambda *_args, **_kwargs: graph)
  monkeypatch.setattr(
    memory_runner, "_reconcile_app_owned_docs", lambda *_args: ([], []),
  )
  monkeypatch.setattr(memory_runner, "_redacted_chats", lambda: chats)
  monkeypatch.setattr(memory_runner, "_remember_pending_chats", lambda _chats: None)
  monkeypatch.setattr(memory_runner, "_pending_read_traces", lambda: [])
  monkeypatch.setattr(memory_runner, "_audit_reads", lambda *_args: [])
  monkeypatch.setattr(
    memory_runner,
    "_proposal_batch",
    lambda _staging, remaining, _audits: remaining[:2],
  )

  def propose(_app_id, _staging, batch, _audits, _providers):
    proposed_batches.append([chat["id"] for chat in batch])
    return memory_runner.ProposalOutcome(
      "ok",
      {
        "updates": [],
        "deletes": [],
        "summary": f"Processed {len(batch)} chats.",
        "followups": [],
        "read_audits": [],
        "self_review": _self_review(),
      },
      "codex",
      "gpt-test",
      [],
    )

  monkeypatch.setattr(memory_runner, "_proposal", propose)
  monkeypatch.setattr(memory_runner, "_known_chat_sources", lambda _path: set())
  monkeypatch.setattr(memory_runner, "_repair_orphans", lambda *_args: [])
  monkeypatch.setattr(
    memory_runner,
    "publish",
    lambda _staging: (
      published.append("publish") or {"commit": "next", "changed": True}
    ),
  )
  monkeypatch.setattr(
    memory_runner,
    "_acknowledge_pending_chats",
    lambda selected: acknowledged.extend(chat["id"] for chat in selected),
  )
  monkeypatch.setattr(
    memory_runner, "_record_run_status", lambda status: statuses.append(status),
  )
  monkeypatch.setattr(memory_runner, "_append_update_log", lambda *_args: None)
  monkeypatch.setattr(
    memory_runner,
    "_record_recall_audits",
    lambda *_args, **_kwargs: None,
  )
  monkeypatch.setattr(memory_runner, "_live_policy", lambda _app_id: (4, 4))
  monkeypatch.setattr(memory_runner, "_night_policy", lambda _app_id: (6, 6))

  assert asyncio.run(memory_runner.run()) == 0
  assert proposed_batches == [
    ["chat-0", "chat-1"],
    ["chat-2", "chat-3"],
    ["chat-4"],
  ]
  assert published == ["publish"]
  assert acknowledged == [chat["id"] for chat in chats]
  assert statuses[-1]["status"] == "published"
  assert statuses[-1]["source_chat_count"] == 5
  assert statuses[-1]["proposal_batch_count"] == 3
  assert statuses[-1]["deferred_chat_count"] == 0


def test_run_publishes_accepted_batches_and_defers_topology_rejection(
  monkeypatch, tmp_path,
):
  chats = [{"id": f"chat-{index}"} for index in range(3)]
  acknowledged = []
  statuses = []
  graph = {"nodes": [], "edges": [], "problems": []}
  apply_count = 0

  monkeypatch.setattr(memory_runner, "_app_id", lambda: 57)
  monkeypatch.setattr(memory_runner, "APP_TOKEN", "scoped-token")
  monkeypatch.setattr(memory_runner, "_app_active", lambda _app_id: True)
  monkeypatch.setattr(
    memory_runner, "ready_pointer", lambda: {"commit": "ready"},
  )
  monkeypatch.setattr(
    memory_runner, "start_staging", lambda _seed: ("run-1", tmp_path),
  )
  monkeypatch.setattr(memory_runner, "discard_staging", lambda _path: None)
  monkeypatch.setattr(memory_runner, "build_graph", lambda *_args, **_kwargs: graph)
  monkeypatch.setattr(
    memory_runner, "_reconcile_app_owned_docs", lambda *_args: ([], []),
  )
  monkeypatch.setattr(memory_runner, "_redacted_chats", lambda: chats)
  monkeypatch.setattr(memory_runner, "_remember_pending_chats", lambda _chats: None)
  monkeypatch.setattr(memory_runner, "_pending_read_traces", lambda: [])
  monkeypatch.setattr(memory_runner, "_audit_reads", lambda *_args: [])
  monkeypatch.setattr(
    memory_runner,
    "_proposal_batch",
    lambda _staging, remaining, _audits: remaining[:2],
  )
  monkeypatch.setattr(
    memory_runner,
    "_proposal",
    lambda *_args: memory_runner.ProposalOutcome(
      "ok",
      {
        "updates": [],
        "deletes": [],
        "summary": "Processed a batch.",
        "followups": [],
        "read_audits": [],
      },
      "codex",
      "gpt-test",
      [{"provider": "codex", "model": "gpt-test", "supported": True}],
    ),
  )

  def apply(proposal, *_args, **_kwargs):
    nonlocal apply_count
    apply_count += 1
    if apply_count == 2:
      raise memory_runner.ProposalValidationError(
        "topology_regression", "specific routing would regress",
      )
    return proposal, [], [], graph

  monkeypatch.setattr(
    memory_runner,
    "_apply_validated_proposal",
    lambda _staging, proposal, **kwargs: apply(proposal, **kwargs),
  )
  monkeypatch.setattr(memory_runner, "_known_chat_sources", lambda _path: set())
  monkeypatch.setattr(memory_runner, "_repair_orphans", lambda *_args: [])
  monkeypatch.setattr(
    memory_runner,
    "publish",
    lambda _staging: {"commit": "next", "changed": True},
  )
  monkeypatch.setattr(
    memory_runner,
    "_acknowledge_pending_chats",
    lambda selected: acknowledged.extend(chat["id"] for chat in selected),
  )
  monkeypatch.setattr(
    memory_runner, "_record_run_status", lambda status: statuses.append(status),
  )
  monkeypatch.setattr(memory_runner, "_append_update_log", lambda *_args: None)
  monkeypatch.setattr(
    memory_runner,
    "_record_recall_audits",
    lambda *_args, **_kwargs: None,
  )
  monkeypatch.setattr(memory_runner, "_live_policy", lambda _app_id: (4, 4))
  monkeypatch.setattr(memory_runner, "_night_policy", lambda _app_id: (6, 6))

  assert asyncio.run(memory_runner.run()) == 0
  assert acknowledged == ["chat-0", "chat-1"]
  assert statuses[-1]["status"] == "published"
  assert statuses[-1]["source_chat_count"] == 2
  assert statuses[-1]["deferred_chat_count"] == 1
  assert statuses[-1]["deferred_reason"] == "topology_regression"
  assert statuses[-1]["deferred_attempted_agents"][-1][
    "rejection_code"
  ] == "topology_regression"


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
  assert stats["continuation_misses"] == 0
  assert stats["selection_misses"] == 0
  assert stats["overreaches"] == 1
  assert stats["overreach_rate"] == 1.0
  assert stats["no_memory_rate"] == 0.0
  assert stats["model_to_host_selection_override_rate"] == 0.0
  assert stats["graph_nodes"] == 3
  assert stats["live_policy"] == {"breadth": 4, "depth": 4}
  assert stats["night_policy"] == {"breadth": 6, "depth": 6}


def test_recall_stats_distinguish_unopened_frontier_from_route_miss(
  monkeypatch, tmp_path,
):
  state = tmp_path / "app-state"
  monkeypatch.setattr(memory_runner, "STATE", state)
  monkeypatch.setattr(memory_runner, "_RECALL_STATS", state / "recall-stats.json")
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
    "deep": {"selected": ["notes/answer.md"], "stop_reason": "navigator_finished"},
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

  memory_runner._record_recall_audits(
    "run-1", audits, proposal, {"nodes": [], "edges": []},
    live_policy=(4, 4), night_policy=(6, 6),
  )

  stats = json.loads((state / "recall-stats.json").read_text())
  assert stats["continuation_misses"] == 1
  assert stats["route_misses"] == 0
  assert stats["selection_misses"] == 0
  assert stats["recent"][0]["miss_class"] == "continuation"


def test_prompt_budget_preserves_routes_and_trims_note_bodies_before_chat(
  monkeypatch, tmp_path,
):
  monkeypatch.setattr(memory_runner, "_MAX_PROMPT_DATA_CHARS", 1400)
  monkeypatch.setattr(memory_runner, "_maintenance_flags", lambda _staging: [])
  monkeypatch.setattr(memory_runner, "_graph_catalog", lambda _staging: [
    {
      "id": "index",
      "path": "index.md",
      "title": "Memory",
      "description": "Root",
      "content": "r" * 200,
    },
    {
      "id": "topic",
      "path": "mocs/topic.md",
      "title": "Topic",
      "description": "Route",
      "content": "r" * 200,
    },
    *[
      {
        "id": f"note-{index}",
        "path": f"notes/note-{index}.md",
        "title": f"Note {index}",
        "description": "Existing fact",
        "content": "n" * 250,
      }
      for index in range(3)
    ],
  ])
  chats = [{
    "id": "chat-one",
    "title": "Useful chat",
    "messages": [{"role": "user", "text": "A durable fact"}],
  }]

  encoded, included = memory_runner._proposal_envelope(tmp_path, chats, [])
  payload = json.loads(encoded)

  assert included == chats
  assert payload["redacted_recent_chats"][0]["source_handle"] == "chat:c01"
  assert len(payload["existing_graph"]) == 5
  assert payload["existing_graph"][0]["content"] == "r" * 200
  assert payload["existing_graph"][1]["content"] == "r" * 200
  assert all(
    "content" not in item for item in payload["existing_graph"][2:]
  )
  assert len(payload["existing_note_contents"]) < 3
  assert len(encoded) <= 1400


def test_prompt_budget_never_silently_drops_required_routes(
  monkeypatch, tmp_path,
):
  monkeypatch.setattr(memory_runner, "_MAX_PROMPT_DATA_CHARS", 300)
  monkeypatch.setattr(memory_runner, "_maintenance_flags", lambda _staging: [])
  monkeypatch.setattr(memory_runner, "_graph_catalog", lambda _staging: [{
    "id": "index",
    "path": "index.md",
    "title": "Memory",
    "description": "Root",
    "content": "r" * 500,
  }])

  with pytest.raises(memory_runner.ProposalValidationError) as raised:
    memory_runner._proposal_envelope(tmp_path, [], [])

  assert raised.value.code == "routing_context_over_budget"


def test_prompt_budget_skips_oversized_chat_and_keeps_trying(monkeypatch, tmp_path):
  monkeypatch.setattr(memory_runner, "_MAX_PROMPT_DATA_CHARS", 500)
  monkeypatch.setattr(memory_runner, "_maintenance_flags", lambda _staging: [])
  monkeypatch.setattr(memory_runner, "_graph_catalog", lambda _staging: [])
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

  encoded, included = memory_runner._proposal_envelope(tmp_path, chats, [])
  payload = json.loads(encoded)

  assert included == [chats[1]]
  assert [item["source_handle"] for item in payload["redacted_recent_chats"]] == [
    "chat:c02",
  ]


def test_prompt_budget_returns_valid_envelope_when_every_chat_is_oversized(
  monkeypatch, tmp_path,
):
  monkeypatch.setattr(memory_runner, "_MAX_PROMPT_DATA_CHARS", 350)
  monkeypatch.setattr(memory_runner, "_maintenance_flags", lambda _staging: [])
  monkeypatch.setattr(memory_runner, "_graph_catalog", lambda _staging: [])
  chats = [{
    "id": f"large-{index}",
    "messages": [{"role": "user", "text": "x" * 1000}],
  } for index in range(3)]

  encoded, included = memory_runner._proposal_envelope(tmp_path, chats, [])

  assert len(encoded) <= 350
  assert included == []
  assert json.loads(encoded)["redacted_recent_chats"] == []


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
