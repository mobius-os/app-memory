"""The nightly consolidation lane: map neighborhoods, deferrals, body retries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import memory_runner
from memory_text_provider import TextResult


def _reviewed(extra: dict) -> dict:
  return {
    "summary": "ok",
    "followups": [],
    "read_audits": [],
    "updates": [],
    "links": [],
    "deletes": [],
    "self_review": {
      "hardest_decision": "none",
      "possibly_missed": "none",
      "prompt_change": "none",
      "next_experiment": "none",
    },
    **extra,
  }


def _note(path: Path, slug: str, moc: str, body: str = "Body") -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(
    f"---\ntype: note\ntitle: {slug}\ndescription: {slug} claim\n"
    f"mocs: [{moc}]\nsource: [chat:known]\n---\n{body}\n",
    encoding="utf-8",
  )


def _graph(staging: Path, mocs: dict[str, list[str]]) -> dict:
  staging.mkdir(parents=True, exist_ok=True)
  nodes = []
  for moc, members in mocs.items():
    nodes.append({
      "id": moc, "path": f"mocs/{moc}.md", "title": moc.title(),
      "description": f"{moc} map", "type": "moc", "mocs": [],
    })
    (staging / "mocs").mkdir(parents=True, exist_ok=True)
    (staging / "mocs" / f"{moc}.md").write_text(
      f"# {moc}\n\n" + "".join(f"- [[{m}]] — cue\n" for m in members),
      encoding="utf-8",
    )
    for member in members:
      _note(staging / "notes" / f"{member}.md", member, moc)
      nodes.append({
        "id": member, "path": f"notes/{member}.md", "title": member,
        "description": f"{member} claim", "type": "note", "mocs": [moc],
      })
  graph = {"nodes": nodes, "edges": [], "problems": []}
  (staging / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
  return graph


@pytest.fixture
def state(monkeypatch, tmp_path):
  state_dir = tmp_path / "state"
  state_dir.mkdir()
  monkeypatch.setattr(memory_runner, "STATE", state_dir)
  monkeypatch.setattr(
    memory_runner, "_CONSOLIDATION_CURSOR", state_dir / "consolidation-cursor.json",
  )
  return state_dir


def test_consolidation_item_carries_whole_neighborhood_and_own_leads(
  state, tmp_path,
):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["alpha-fact", "beta-fact"], "people": ["gamma"]})
  log_dir = state / "update-log"
  log_dir.mkdir()
  (log_dir / "2026-09-04.jsonl").write_text(
    json.dumps({
      "followups": [
        "Enrich alpha-fact once its body is supplied.",
        "Unrelated lead about something else.",
      ],
      "writer_self_reviews": [
        {"next_experiment": "Cross-link beta-fact from projects.", "possibly_missed": "none"},
      ],
    }) + "\n",
    encoding="utf-8",
  )

  item = memory_runner._consolidation_item(staging, "mocs/projects.md", {})

  assert item["moc"]["path"] == "mocs/projects.md"
  assert item["member_paths"] == ["notes/alpha-fact.md", "notes/beta-fact.md"]
  assert [entry["path"] for entry in item["note_contents"]] == item["member_paths"]
  assert item["leads"] == [
    "Enrich alpha-fact once its body is supplied.",
    "Cross-link beta-fact from projects.",
  ]
  payload = json.loads(memory_runner._proposal_data(staging, [], [], consolidation=item))
  assert "note_contents" not in payload["consolidation"]
  assert {entry["path"] for entry in payload["existing_note_contents"]} == set(
    item["member_paths"]
  )


def test_candidates_rotate_least_recently_consolidated_first(state, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["a"], "people": ["b"], "tools": ["c"]})
  memory_runner._record_consolidation_attempt("mocs/people.md")
  memory_runner._record_consolidation_attempt("mocs/projects.md")

  assert memory_runner._consolidation_candidates(staging) == [
    "mocs/tools.md", "mocs/people.md", "mocs/projects.md",
  ]


def test_oversized_neighborhood_visits_every_member_before_repeating(state, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": list("abcdef")})
  for slug in "abcdef":
    _note(staging / "notes" / f"{slug}.md", slug, "projects", "x" * 60_000)

  passes = []
  for _ in range(6):
    item = memory_runner._consolidation_item(staging, "mocs/projects.md", {})
    passes.append([Path(path).stem for path in item["member_paths"]])
    memory_runner._record_consolidation_attempt(
      "mocs/projects.md", omitted=item["omitted_member_paths"],
    )

  assert passes == [["a", "b"], ["c", "d"], ["e", "f"]] * 2


def test_member_rotation_keeps_waiting_order_when_members_change(state, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["a", "b", "c", "new"]})
  memory_runner._record_consolidation_attempt(
    "mocs/projects.md",
    omitted=["notes/deleted.md", "notes/c.md", "notes/b.md"],
  )

  item = memory_runner._consolidation_item(staging, "mocs/projects.md", {})

  assert item["member_paths"] == [
    "notes/c.md", "notes/b.md", "notes/a.md", "notes/new.md",
  ]


def test_lanes_rotate_and_a_rejected_item_defers_without_blocking(
  state, monkeypatch, tmp_path,
):
  staging = tmp_path / "staging"
  graph = _graph(staging, {"projects": ["a"], "people": ["b"]})
  chats = [
    {"id": f"chat-{index}", "messages": [{"role": "user", "text": "hello"}]}
    for index in range(3)
  ]
  audits = [{"read_id": "r1"}, {"read_id": "r2"}]
  seen: list[tuple[str, str | None]] = []

  def proposal(_app_id, _staging, batch, batch_audits, _providers, _deadline, **kwargs):
    kind = "audit" if batch_audits else "chat" if batch else "consolidate"
    moc = (kwargs.get("consolidation") or {}).get("moc", {}).get("path")
    seen.append((kind, moc))
    return memory_runner.ProposalOutcome(
      "ok", _reviewed({"summary": kind}), "codex", "gpt-test", [{"provider": "codex"}],
    )

  monkeypatch.setattr(memory_runner, "_proposal", proposal)
  applied = 0

  def apply(_staging, value, **_kwargs):
    nonlocal applied
    applied += 1
    if applied == 2:  # the first chat item
      raise memory_runner.ProposalValidationError(
        "topology_regression", "would demote a routed node",
      )
    return value, [], [], graph

  monkeypatch.setattr(memory_runner, "_apply_validated_proposal", apply)
  usage_reads = []
  monkeypatch.setattr(memory_runner, "note_usage_evidence",
                      lambda: usage_reads.append(1) or {})

  result = memory_runner._consolidate_batches(
    57, staging, graph, chats, audits, memory_runner.ProviderPool([]),
  )

  # A rejected item is not progress, so its lane keeps its turn once more;
  # otherwise the three lanes take turns.
  assert [kind for kind, _ in seen] == [
    "audit", "chat", "chat", "consolidate", "audit", "chat", "consolidate",
  ]
  assert [moc for kind, moc in seen if kind == "consolidate"] == [
    "mocs/people.md", "mocs/projects.md",
  ]
  assert [chat["id"] for chat in result.accepted_chats] == ["chat-1", "chat-2"]
  assert [chat["id"] for chat in result.remaining_chats] == ["chat-0"]
  assert result.rejected_chat_count == 1
  assert result.accepted_mocs == ["mocs/people.md", "mocs/projects.md"]
  assert result.consolidation_batch_count == 2
  # The whole audit history is read once per run, not once per map.
  assert usage_reads == [1]
  assert result.deferred_reason == "topology_regression"
  cursor = json.loads((state / "consolidation-cursor.json").read_text())
  assert set(cursor["attempted"]) == {"mocs/people.md", "mocs/projects.md"}


def test_lane_stops_only_after_consecutive_rejections(state, monkeypatch, tmp_path):
  staging = tmp_path / "staging"
  graph = _graph(staging, {})
  chats = [
    {"id": f"chat-{index}", "messages": [{"role": "user", "text": "hello"}]}
    for index in range(6)
  ]
  monkeypatch.setattr(
    memory_runner, "_proposal",
    lambda *_args, **_kwargs: memory_runner.ProposalOutcome(
      "degraded", None, None, None, [{"provider": "codex", "rejection_code": "x"}],
    ),
  )

  result = memory_runner._consolidate_batches(
    57, staging, graph, chats, [], memory_runner.ProviderPool([]),
  )

  assert result.rejected_chat_count == memory_runner._LANE_REJECTION_LIMIT
  assert len(result.remaining_chats) == 6
  assert result.deferred_reason == "no_valid_text_only_proposal"


def test_clock_running_out_is_reported_as_the_deadline(state, monkeypatch, tmp_path):
  staging = tmp_path / "staging"
  graph = _graph(staging, {})
  chats = [{"id": "chat-0", "messages": [{"role": "user", "text": "hello"}]}]
  monkeypatch.setattr(
    memory_runner, "_proposal",
    lambda *_args, **_kwargs: memory_runner.ProposalOutcome(
      "degraded", None, None, None,
      [{"provider": "codex", "failure_code": "work_window_elapsed"}],
    ),
  )

  result = memory_runner._consolidate_batches(
    57, staging, graph, chats, [], memory_runner.ProviderPool([]),
  )

  assert result.deferred_reason == "work_window_elapsed"
  assert result.rejected_chat_count == 0
  assert [chat["id"] for chat in result.remaining_chats] == ["chat-0"]


def test_explicit_body_request_continues_same_item_before_any_edit(
  state, monkeypatch, tmp_path,
):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["alpha-fact"]})
  chat = {"id": "known", "messages": [{"role": "user", "text": "unrelated words"}]}
  prompts: list[str] = []
  update = {
    "path": "notes/alpha-fact.md",
    "content": "---\ntype: note\ntitle: Alpha\nmocs: [projects]\nsource: [chat:c01]\n---\nNew\n",
  }

  def text(_choice, prompt, **_kwargs):
    prompts.append(prompt)
    if len(prompts) == 1:
      return TextResult(json.dumps({"request_note_bodies": ["notes/alpha-fact.md"]}))
    return TextResult(json.dumps(_reviewed({"updates": [update]})))

  monkeypatch.setattr(memory_runner, "run_text", text)
  monkeypatch.setattr(memory_runner, "_known_chat_sources", lambda _path: set())
  monkeypatch.setattr(memory_runner, "_known_deleted_source_ids", lambda _path: set())
  monkeypatch.setattr(memory_runner, "_known_deleted_source", lambda _path: False)
  providers = memory_runner.ProviderPool([
    {"provider": "claude", "model": "opus"},
    {"provider": "codex", "model": "gpt-test"},
  ])

  outcome = memory_runner._proposal(57, staging, [chat], [], providers)

  assert outcome.status == "ok"
  assert outcome.provider == "claude"
  assert len(prompts) == 2
  assert "notes/alpha-fact.md" not in json.loads(prompts[0].split("DATA:\n", 1)[1])[
    "existing_note_contents"
  ].__str__()
  assert any(
    entry["path"] == "notes/alpha-fact.md"
    for entry in json.loads(prompts[1].split("DATA:\n", 1)[1])["existing_note_contents"]
  )
  assert outcome.attempted_agents[0]["outcome"] == "context_requested"
  assert outcome.attempted_agents[0]["requested_note_paths"] == ["notes/alpha-fact.md"]
  assert outcome.attempted_agents[1]["outcome"] == "accepted"


def _map_rewrite(members: list[str], extra: str = "") -> str:
  return (
    "---\ntitle: Projects\ntype: moc\n---\n# Projects\n\n"
    + "".join(f"- [[{m}]] — clearer cue\n" for m in members)
    + extra
  )


def test_consolidation_item_may_rewrite_its_own_map(state, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["alpha-fact", "beta-fact"]})
  item = memory_runner._consolidation_item(staging, "mocs/projects.md", {})
  editable = memory_runner._editable_map(item)
  assert editable == {"path": "mocs/projects.md", "members": {"alpha-fact", "beta-fact"}}

  proposal = _reviewed({
    "updates": [
      {"path": "mocs/projects.md", "content": _map_rewrite(["alpha-fact", "beta-fact"])},
    ],
  })
  normalized = memory_runner._normalize_proposal(
    proposal, allowed_chat_ids=set(), editable_map=editable,
  )
  changed, _deleted = memory_runner._apply_normalized_proposal(staging, normalized)

  assert changed == ["mocs/projects.md"]
  assert "clearer cue" in (staging / "mocs" / "projects.md").read_text()


def test_map_rewrite_keeps_every_member_it_did_not_decide_about(state, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["alpha-fact", "beta-fact", "gamma-fact"]})
  editable = memory_runner._editable_map(
    memory_runner._consolidation_item(staging, "mocs/projects.md", {}),
  )

  silently_dropped = _reviewed({
    "updates": [{"path": "mocs/projects.md", "content": _map_rewrite(["alpha-fact"])}],
  })
  with pytest.raises(memory_runner.ProposalValidationError) as error:
    memory_runner._normalize_proposal(
      silently_dropped, allowed_chat_ids=set(), editable_map=editable,
    )
  assert error.value.code == "map_member_unlinked"
  assert "beta-fact" in str(error.value) and "gamma-fact" in str(error.value)

  decided = _reviewed({
    "updates": [{"path": "mocs/projects.md", "content": _map_rewrite(["alpha-fact"])}],
    "deletes": ["notes/beta-fact.md", "notes/gamma-fact.md"],
  })
  normalized = memory_runner._normalize_proposal(
    decided, allowed_chat_ids=set(), editable_map=editable,
  )
  assert normalized["deletes"] == ["notes/beta-fact.md", "notes/gamma-fact.md"]


def test_only_the_supplied_map_becomes_writable(state, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["alpha-fact"], "people": ["gamma"]})
  editable = memory_runner._editable_map(
    memory_runner._consolidation_item(staging, "mocs/projects.md", {}),
  )

  other_map = _reviewed({
    "updates": [{"path": "mocs/people.md", "content": _map_rewrite(["gamma"])}],
  })
  with pytest.raises(memory_runner.ProposalValidationError) as error:
    memory_runner._normalize_proposal(
      other_map, allowed_chat_ids=set(), editable_map=editable,
    )
  assert error.value.code == "invalid_memory_file"

  not_a_map = _reviewed({
    "updates": [{"path": "mocs/projects.md", "content": "# Projects\n\n- [[alpha-fact]]\n"}],
  })
  with pytest.raises(memory_runner.ProposalValidationError) as error:
    memory_runner._normalize_proposal(
      not_a_map, allowed_chat_ids=set(), editable_map=editable,
    )
  assert error.value.code == "malformed_frontmatter"
  assert memory_runner._editable_map(None) is None


def test_shared_vocabulary_never_prefills_unrequested_bodies(state, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": [f"same-memory-project-{i}" for i in range(40)]})
  data = json.loads(memory_runner._proposal_data(staging, [{
    "id": "known", "title": "same memory project", "summary": "same memory project",
    "captures": [{"text": "same memory project"}],
  }]))
  assert data["existing_note_contents"] == []
  assert len(data["existing_graph"]["notes"]) == 40
  assert all(len(row) == 2 and row[1] for row in data["existing_graph"]["notes"])


def test_audit_receives_exact_live_and_deep_current_bodies_not_unselected_frontier(state, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["live", "deep", "pruned"]})
  data = json.loads(memory_runner._proposal_data(staging, [], [{
    "live": {"selected": ["notes/live.md"], "frontier_at_stop": [{"path": "notes/pruned.md"}]},
    "deep": {"selected": ["notes/deep.md"]},
  }]))
  assert {row["path"] for row in data["existing_note_contents"]} == {"notes/live.md", "notes/deep.md"}


@pytest.mark.parametrize("response,code", [
  ({"request_note_bodies": []}, "invalid_note_body_request"),
  ({"request_note_bodies": ["../../outside.md"]}, "invalid_note_body_request"),
  ({"request_note_bodies": ["notes/missing.md"]}, "invalid_note_body_request"),
  ({"request_note_bodies": [42]}, "invalid_note_body_request"),
  ({"request_note_bodies": ["notes/alpha.md"], "deletes": ["notes/alpha.md"]}, "invalid_note_body_request"),
])
def test_invalid_requests_cannot_edit_or_escape_catalogue(state, monkeypatch, tmp_path, response, code):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["alpha"]})
  before = (staging / "notes/alpha.md").read_bytes()
  monkeypatch.setattr(memory_runner, "run_text", lambda *a, **kw: TextResult(json.dumps(response)))
  outcome = memory_runner._proposal(57, staging, [], [], memory_runner.ProviderPool([{"provider": "codex"}]))
  assert outcome.status == "degraded"
  assert outcome.attempted_agents[0]["rejection_code"] == code
  assert (staging / "notes/alpha.md").read_bytes() == before


def test_repeated_request_without_new_context_cannot_loop(state, monkeypatch, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["alpha"]})
  monkeypatch.setattr(memory_runner, "run_text", lambda *a, **kw: TextResult(json.dumps({"request_note_bodies": ["notes/alpha.md"]})))
  outcome = memory_runner._proposal(57, staging, [], [], memory_runner.ProviderPool([{"provider": "codex"}]))
  assert outcome.status == "degraded"
  assert len(outcome.attempted_agents) == 2
  assert outcome.attempted_agents[-1]["rejection_code"] == "invalid_note_body_request"


def test_context_safety_boundary_does_not_silently_truncate_requested_notes(state, monkeypatch, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["alpha"]})
  monkeypatch.setattr(memory_runner, "_MAX_RELATED_NOTE_CONTENT_CHARS", 1)
  monkeypatch.setattr(memory_runner, "run_text", lambda *a, **kw: TextResult(json.dumps({"request_note_bodies": ["notes/alpha.md"]})))
  outcome = memory_runner._proposal(57, staging, [], [], memory_runner.ProviderPool([{"provider": "codex"}]))
  assert outcome.status == "degraded"
  assert outcome.attempted_agents[0]["rejection_code"] == "note_context_limit"


def test_context_request_at_deadline_remains_unfinished_not_failed_judgment(state, monkeypatch, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["alpha"]})
  clock = [0]
  monkeypatch.setattr(memory_runner.time, "monotonic", lambda: clock[0])
  def request(*args, **kwargs):
    clock[0] = 10
    return TextResult(json.dumps({"request_note_bodies": ["notes/alpha.md"]}))
  monkeypatch.setattr(memory_runner, "run_text", request)
  outcome = memory_runner._proposal(57, staging, [], [], memory_runner.ProviderPool([{"provider": "codex"}]), deadline=10)
  assert outcome.status == "degraded"
  assert outcome.attempted_agents[-1]["failure_code"] == "work_window_elapsed"
  assert memory_runner._model_ran(outcome.attempted_agents) is False


def test_unseen_note_edit_remains_rejected_instead_of_becoming_body_request(state, monkeypatch, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["alpha"]})
  content = (staging / "notes/alpha.md").read_text()
  monkeypatch.setattr(memory_runner, "run_text", lambda *a, **kw: TextResult(json.dumps(_reviewed({
    "updates": [{"path": "notes/alpha.md", "content": content}],
  }))))
  outcome = memory_runner._proposal(57, staging, [], [], memory_runner.ProviderPool([{"provider": "codex"}]))
  assert outcome.status == "degraded"
  assert outcome.attempted_agents[0]["rejection_code"] == "note_body_not_supplied"


def test_unseen_note_cannot_be_deleted_from_title_alone(state, monkeypatch, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["alpha"]})
  monkeypatch.setattr(memory_runner, "run_text", lambda *a, **kw: TextResult(json.dumps(_reviewed({
    "deletes": ["notes/alpha.md"],
  }))))
  outcome = memory_runner._proposal(57, staging, [], [], memory_runner.ProviderPool([{"provider": "codex"}]))
  assert outcome.status == "degraded"
  assert outcome.attempted_agents[0]["rejection_code"] == "note_body_not_supplied"
  assert (staging / "notes/alpha.md").is_file()


def test_successive_new_body_requests_are_not_an_arbitrary_one_retry_quota(state, monkeypatch, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["alpha", "beta"]})
  responses = iter([
    {"request_note_bodies": ["notes/alpha.md"]},
    {"request_note_bodies": ["notes/beta.md"]},
    _reviewed({}),
  ])
  monkeypatch.setattr(memory_runner, "run_text", lambda *a, **kw: TextResult(json.dumps(next(responses))))
  outcome = memory_runner._proposal(57, staging, [], [], memory_runner.ProviderPool([{"provider": "codex"}]))
  assert outcome.status == "ok"
  assert [a["outcome"] for a in outcome.attempted_agents] == ["context_requested", "context_requested", "accepted"]


def _root_item(staging):
  graph = _graph(staging, {"projects": ["alpha"]})
  graph["nodes"].append({"id": "index", "path": "index.md", "type": "moc"})
  (staging / "graph.json").write_text(json.dumps(graph))
  root = ("---\ntype: moc\ntitle: Home\n---\n# Home\n\nStale prose.\n"
          "- [[projects]] — Projects\n\n"
          + memory_runner._UNFILED_START + "\n- [[memory-unfiled]] — Unfiled\n"
          + memory_runner._UNFILED_END + "\n")
  (staging / "index.md").write_text(root)
  return root, memory_runner._consolidation_item(staging, "index.md", {})


def test_root_joins_existing_review_rotation_and_can_correct_prose(state, tmp_path):
  staging = tmp_path / "staging"
  root, item = _root_item(staging)
  assert "index.md" in memory_runner._consolidation_candidates(staging)
  assert item["moc"]["content"] == root
  rewrite = root.replace("Stale prose.", "Accurate guidance.")
  value = memory_runner._normalize_proposal(_reviewed({
    "updates": [{"path": "index.md", "content": rewrite}],
  }), allowed_chat_ids=set(), editable_map=memory_runner._editable_map(item))
  assert value["updates"][0]["content"] == rewrite
  memory_runner._record_consolidation_fingerprint(staging, "index.md")
  assert "index.md" in memory_runner._read_consolidation_cursor()["fingerprints"]


@pytest.mark.parametrize("change,code", [
  ("unlink", "map_member_unlinked"),
  ("managed", "managed_root_block_changed"),
  ("duplicate_marker", "managed_root_block_changed"),
  ("delete", "invalid_deletion"),
  ("blind", "invalid_memory_file"),
  ("conflicting", "conflicting_root_edits"),
])
def test_root_review_preserves_routing_managed_block_and_authority(state, tmp_path, change, code):
  root, item = _root_item(tmp_path / "staging")
  content = root
  if change == "unlink":
    content = root.replace("[[projects]]", "Projects")
  elif change == "managed":
    content = root.replace("— Unfiled", "— Changed")
  elif change == "duplicate_marker":
    content += memory_runner._UNFILED_START
  proposal = _reviewed({"updates": [{"path": "index.md", "content": content}]})
  if change == "conflicting":
    proposal["links"] = [{"from": "index.md", "to": "mocs/projects.md", "cue": "Changed cue"}]
  if change == "delete":
    proposal.update(updates=[], deletes=["index.md"])
  with pytest.raises(memory_runner.ProposalValidationError) as error:
    memory_runner._normalize_proposal(proposal, allowed_chat_ids=set(),
      editable_map=None if change == "blind" else memory_runner._editable_map(item))
  assert error.value.code == code
