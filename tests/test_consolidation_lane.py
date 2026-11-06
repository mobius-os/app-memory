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

  assert memory_runner._consolidation_candidates(staging, {}) == [
    "mocs/tools.md", "mocs/people.md", "mocs/projects.md",
  ]


def _shared_graph(staging: Path) -> None:
  """Two maps that share one note, as a fact filed under two questions is."""
  graph = _graph(staging, {"people": ["shared-fact", "b"], "projects": ["c"]})
  (staging / "mocs" / "projects.md").write_text(
    "# projects\n\n- [[c]] — cue\n- [[shared-fact]] — cue\n", encoding="utf-8",
  )
  for node in graph["nodes"]:
    if node["id"] == "shared-fact":
      node["mocs"] = ["people", "projects"]
  (staging / "graph.json").write_text(json.dumps(graph), encoding="utf-8")


def _settle(staging: Path, *mocs: str, usage: dict | None = None) -> None:
  """Record maps as the writer left them after an accepted consolidation."""
  for moc in mocs:
    memory_runner._record_consolidation_attempt(f"mocs/{moc}.md")
    memory_runner._record_consolidation_fingerprint(
      staging, f"mocs/{moc}.md", usage or {},
    )
  memory_runner._mark_consolidated_through_publication(
    [f"mocs/{moc}.md" for moc in mocs],
  )


def _reviewed_long_ago(moc: str) -> None:
  """Backdate a map's last successful review, as after many busy nights."""
  cursor = json.loads(memory_runner._CONSOLIDATION_CURSOR.read_text())
  for key in ("attempted", "reviewed"):
    cursor[key][f"mocs/{moc}.md"] = "2026-09-30T04:30:00+00:00"
  memory_runner._CONSOLIDATION_CURSOR.write_text(json.dumps(cursor))


def _publish_followups(
  state: Path, followups: list[str], *, consolidated: list[str],
  day: str = "2026-10-30", **fields,
) -> None:
  """Append one writer update record and close it as publication does."""
  from datetime import UTC, datetime

  log = state / "update-log"
  log.mkdir(exist_ok=True)
  record = {
    "timestamp": datetime.now(UTC).isoformat(),
    "followups": followups,
    "consolidated_mocs": [f"mocs/{moc}.md" for moc in consolidated],
    **fields,
  }
  with (log / f"{day}.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record) + "\n")
  memory_runner._mark_consolidated_through_publication(
    [f"mocs/{moc}.md" for moc in consolidated],
  )


def test_a_review_that_found_nothing_new_cannot_wake_its_neighbors(state, tmp_path):
  staging = tmp_path / "staging"
  _shared_graph(staging)
  _settle(staging, "people", "projects")

  # A night re-reads `people` alone, changes nothing and restates a standing
  # note about the fact it shares with `projects`.
  _publish_followups(
    state, ["STANDING (people): keep shared-fact apart from b."],
    consolidated=["people"], input_leads=[],
  )

  assert memory_runner._consolidation_candidates(staging, {}) == []
  # The lead is not lost: `projects` still receives it when next consolidated.
  item = memory_runner._consolidation_item(staging, "mocs/projects.md", {})
  assert item["leads"] == ["STANDING (people): keep shared-fact apart from b."]


LEAD = "Filed a saved fact; check shared-fact's cue in projects."


@pytest.mark.parametrize("fields", [
  {"input_leads": [LEAD]},  # written while filing a fact or judging a lookup
  {},  # an update record written before input leads were kept
])
def test_followups_written_while_acting_on_new_input_wake_the_maps_they_name(
  state, tmp_path, fields,
):
  staging = tmp_path / "staging"
  _shared_graph(staging)
  _settle(staging, "people", "projects")

  _publish_followups(state, [LEAD], consolidated=["people"], **fields)

  assert memory_runner._consolidation_candidates(staging, {}) == [
    "mocs/projects.md",
  ]


def test_a_map_review_on_a_night_that_filed_facts_cannot_wake_neighbors(
  state, tmp_path,
):
  staging = tmp_path / "staging"
  _shared_graph(staging)
  _settle(staging, "people", "projects")

  # The same night filed a fact (its own lead names nothing here) and
  # re-read `people`, restating a standing note about the shared fact.
  _publish_followups(
    state, ["Filed under people.", "STANDING (people): keep shared-fact."],
    consolidated=["people"], input_leads=["Filed under people."],
  )

  assert memory_runner._consolidation_candidates(staging, {}) == []


def test_a_lead_reaches_its_map_however_many_nights_pass_first(state, tmp_path):
  staging = tmp_path / "staging"
  _shared_graph(staging)
  _settle(staging, "people", "projects")
  _reviewed_long_ago("projects")
  _publish_followups(
    state, ["Projects' cue for c reads stale."], consolidated=["people"],
    day="2026-10-01", input_leads=[],
  )
  # Busy nights elsewhere push it out of the three most recent update logs.
  for day in ("2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"):
    _publish_followups(
      state, ["Routine note about b."], consolidated=["people"], day=day,
      input_leads=[],
    )

  item = memory_runner._consolidation_item(staging, "mocs/projects.md", {})

  assert "Projects' cue for c reads stale." in item["leads"]


def test_a_map_starved_for_nights_still_wakes_for_an_input_lead(state, tmp_path):
  staging = tmp_path / "staging"
  _shared_graph(staging)
  _settle(staging, "people", "projects")
  _reviewed_long_ago("projects")
  lead = "Filed a fact; c in projects needs a sharper cue."
  _publish_followups(
    state, [lead], consolidated=["people"], day="2026-10-01", input_leads=[lead],
  )
  for day in ("2026-10-02", "2026-10-03", "2026-10-04"):
    _publish_followups(
      state, ["Routine note about b."], consolidated=["people"], day=day,
      input_leads=[],
    )

  assert memory_runner._consolidation_candidates(staging, {}) == [
    "mocs/projects.md",
  ]


@pytest.mark.parametrize("upgraded", [False, True])
def test_a_lead_only_map_whose_review_fails_is_retried_with_its_lead(
  state, monkeypatch, tmp_path, upgraded,
):
  staging = tmp_path / "staging"
  _shared_graph(staging)
  _settle(staging, "people", "projects")
  if upgraded:
    # A cursor written before successful reviews were kept apart.
    cursor = json.loads(memory_runner._CONSOLIDATION_CURSOR.read_text())
    cursor.pop("reviewed", None)
    memory_runner._CONSOLIDATION_CURSOR.write_text(json.dumps(cursor))
  lead = "Filed a fact; c in projects needs a sharper cue."
  _publish_followups(state, [lead], consolidated=["people"], input_leads=[lead])
  assert memory_runner._consolidation_candidates(staging, {}) == [
    "mocs/projects.md",
  ]

  # The writer fails on that one item: nothing is accepted.
  graph = json.loads((staging / "graph.json").read_text())
  monkeypatch.setattr(
    memory_runner, "_proposal",
    lambda *_args, **_kwargs: memory_runner.ProposalOutcome(
      status="degraded", proposal=None, provider=None, model=None,
      attempted_agents=[{"provider": "claude", "model": "m", "supported": True}],
    ),
  )
  result = memory_runner._consolidate_batches(
    7, staging, graph, [], [], memory_runner.ProviderPool([]),
  )
  assert (result.accepted_mocs, result.rejected_moc_count) == ([], 1)

  # The next night retries it, and the lead still reaches the review.
  assert memory_runner._consolidation_candidates(staging, {}) == [
    "mocs/projects.md",
  ]
  item = memory_runner._consolidation_item(staging, "mocs/projects.md", {})
  assert lead in item["leads"]

  # Once a review succeeds, the same lead wakes nothing again.
  _settle(staging, "projects")
  assert memory_runner._consolidation_candidates(staging, {}) == []


def test_a_failed_review_still_wakes_and_delivers_after_busy_nights(
  state, monkeypatch, tmp_path,
):
  staging = tmp_path / "staging"
  _shared_graph(staging)
  _settle(staging, "people", "projects")
  _reviewed_long_ago("projects")
  lead = "Filed a fact; c in projects needs a sharper cue."
  _publish_followups(
    state, [lead], consolidated=["people"], day="2026-10-01", input_leads=[lead],
  )
  graph = json.loads((staging / "graph.json").read_text())
  monkeypatch.setattr(
    memory_runner, "_proposal",
    lambda *_args, **_kwargs: memory_runner.ProposalOutcome(
      status="degraded", proposal=None, provider=None, model=None,
      attempted_agents=[{"provider": "claude", "model": "m", "supported": True}],
    ),
  )
  memory_runner._consolidate_batches(
    7, staging, graph, [], [], memory_runner.ProviderPool([]),
  )
  # Busy nights elsewhere push the lead out of the three most recent logs.
  for day in ("2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"):
    _publish_followups(
      state, ["Routine note about b."], consolidated=["people"], day=day,
      input_leads=[],
    )

  assert memory_runner._consolidation_candidates(staging, {}) == [
    "mocs/projects.md",
  ]
  item = memory_runner._consolidation_item(staging, "mocs/projects.md", {})
  assert lead in item["leads"]


def test_rotation_waits_for_usage_evidence_the_writer_has_not_seen(state, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"people": ["a"], "projects": ["b"]})
  usage = {
    "notes/a.md": {"needed": 1, "overreach": 0, "lookups_since_created": 2},
    "notes/b.md": {"needed": 0, "overreach": 0, "lookups_since_created": 2},
  }
  _settle(staging, "people", "projects", usage=usage)

  assert memory_runner._consolidation_candidates(staging, usage) == []

  # A note first appearing in the usage record carries no lookup evidence.
  usage["notes/new.md"] = {"needed": 0, "overreach": 0, "lookups_since_created": 0}
  assert memory_runner._consolidation_candidates(staging, usage) == []

  # A lookup that needed b, for the first time, reopens only b's map.
  needed_b = {**usage, "notes/b.md": {
    "needed": 1, "overreach": 0, "lookups_since_created": 3,
  }, "notes/a.md": {**usage["notes/a.md"], "lookups_since_created": 3}}
  assert memory_runner._consolidation_candidates(staging, needed_b) == [
    "mocs/projects.md",
  ]
  _settle(staging, "projects", usage=needed_b)
  assert memory_runner._consolidation_candidates(staging, needed_b) == []

  # Needed again and again, b reopens its map only as that count doubles.
  def needed(count: int) -> dict:
    return {**needed_b, "notes/b.md": {**needed_b["notes/b.md"], "needed": count}}
  assert memory_runner._consolidation_candidates(staging, needed(2)) == [
    "mocs/projects.md",
  ]
  _settle(staging, "projects", usage=needed(2))
  assert memory_runner._consolidation_candidates(staging, needed(3)) == []

  # Lookups that need neither note reopen a map only as that count doubles:
  # each map rotates in once, oldest first, then it is quiet again.
  doubled = {
    path: {**entry, "lookups_since_created": 4} for path, entry in needed_b.items()
  }
  assert memory_runner._consolidation_candidates(staging, doubled) == [
    "mocs/people.md",
  ]
  _settle(staging, "people", usage=doubled)
  assert memory_runner._consolidation_candidates(staging, doubled) == [
    "mocs/projects.md",
  ]
  _settle(staging, "projects", usage=doubled)
  assert memory_runner._consolidation_candidates(staging, doubled) == []
  more = {
    path: {**entry, "lookups_since_created": 7} for path, entry in doubled.items()
  }
  assert memory_runner._consolidation_candidates(staging, more) == []


def test_rotation_still_serves_waiting_members_and_owed_maintenance(state, tmp_path):
  staging = tmp_path / "staging"
  graph = _graph(staging, {"people": ["a", "b"], "projects": ["c"]})
  _settle(staging, "people", "projects")
  assert memory_runner._consolidation_candidates(staging, {}) == []

  # An oversized map left members out last time: it keeps rotating in.
  memory_runner._record_consolidation_attempt(
    "mocs/projects.md", omitted=["notes/c.md"],
  )
  assert memory_runner._consolidation_candidates(staging, {}) == [
    "mocs/projects.md",
  ]
  memory_runner._record_consolidation_attempt("mocs/projects.md")
  assert memory_runner._consolidation_candidates(staging, {}) == []

  # A fixable flag on a note in no map cannot be cleared by any item.
  graph["nodes"].append({
    "id": "loose", "path": "notes/loose.md", "type": "note", "mocs": ["gone"],
  })
  graph["problems"] = [
    {"kind": "bare_map_entry", "severity": "warning", "node": "loose"},
  ]
  (staging / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
  assert memory_runner._consolidation_candidates(staging, {}) == []

  # A fixable flag on a member keeps that map in rotation until it clears.
  graph["problems"].append(
    {"kind": "description_too_long", "severity": "warning", "node": "b"},
  )
  (staging / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
  assert memory_runner._consolidation_candidates(staging, {}) == [
    "mocs/people.md",
  ]


def test_maps_settled_before_usage_was_recorded_rotate_once_each(state, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"people": ["a"], "projects": ["b"]})
  _settle(staging, "people", "projects")
  cursor = json.loads(memory_runner._CONSOLIDATION_CURSOR.read_text())
  cursor.pop("usage")
  memory_runner._CONSOLIDATION_CURSOR.write_text(json.dumps(cursor))

  assert memory_runner._consolidation_candidates(staging, {}) == ["mocs/people.md"]
  _settle(staging, "people")
  assert memory_runner._consolidation_candidates(staging, {}) == ["mocs/projects.md"]
  _settle(staging, "projects")
  assert memory_runner._consolidation_candidates(staging, {}) == []


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
  assert outcome.attempted_agents[-1]["rejection_code"] == "note_body_request_limit"


def test_full_neighborhood_answers_request_as_over_budget_not_failure(state, monkeypatch, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["alpha", "beta"]})
  _note(staging / "notes/alpha.md", "alpha", "projects", "a" * 500)
  ceiling = len((staging / "notes/alpha.md").read_text()) + 1
  monkeypatch.setattr(memory_runner, "_MAX_RELATED_NOTE_CONTENT_CHARS", ceiling)
  item = memory_runner._consolidation_item(staging, "mocs/projects.md", {})
  assert item["omitted_member_paths"] == ["notes/beta.md"]
  prompts = []
  responses = iter([{"request_note_bodies": ["notes/beta.md"]}, _reviewed({})])
  def answer(choice, prompt, **kwargs):
    prompts.append(prompt)
    return TextResult(json.dumps(next(responses)))
  monkeypatch.setattr(memory_runner, "run_text", answer)
  outcome = memory_runner._proposal(
    57, staging, [], [], memory_runner.ProviderPool([{"provider": "codex"}]),
    consolidation=item,
  )
  assert outcome.status == "ok"
  assert [a["outcome"] for a in outcome.attempted_agents] == ["context_requested", "accepted"]
  assert outcome.attempted_agents[0]["over_budget_note_paths"] == ["notes/beta.md"]
  assert '"note_bodies_over_budget": ["notes/beta.md"]' in prompts[1]
  assert "rejection_code" not in outcome.attempted_agents[0]


def test_over_budget_body_cannot_be_requested_again(state, monkeypatch, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["alpha"]})
  monkeypatch.setattr(memory_runner, "_MAX_RELATED_NOTE_CONTENT_CHARS", 1)
  monkeypatch.setattr(memory_runner, "run_text", lambda *a, **kw: TextResult(json.dumps({"request_note_bodies": ["notes/alpha.md"]})))
  outcome = memory_runner._proposal(57, staging, [], [], memory_runner.ProviderPool([{"provider": "codex"}]))
  assert [a.get("rejection_code") for a in outcome.attempted_agents] == [
    None, "note_body_request_limit",
  ]


def test_initial_audit_bodies_respect_ceiling_and_name_deferred_evidence(state, monkeypatch, tmp_path):
  staging = tmp_path / "staging"
  _graph(staging, {"projects": ["live", "deep"]})
  for slug in ("live", "deep"):
    _note(staging / f"notes/{slug}.md", slug, "projects", "x" * 90_000)
  data = json.loads(memory_runner._proposal_data(staging, [], [{
    "live": {"selected": ["notes/live.md"]}, "deep": {"selected": ["notes/deep.md"]},
  }]))
  assert [row["path"] for row in data["existing_note_contents"]] == ["notes/deep.md"]
  assert data["existing_note_contents"][0]["content"].endswith("x" * 90_000 + "\n")
  assert data["note_bodies_over_budget"] == ["notes/live.md"]
  assert sum(len(row["content"]) for row in data["existing_note_contents"]) <= (
    memory_runner._MAX_RELATED_NOTE_CONTENT_CHARS
  )


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


def test_one_body_request_round_then_the_next_answer_is_final(state, monkeypatch, tmp_path):
  # Each request resends the whole prompt and items run one at a time, so a
  # writer that keeps asking for small bodies cannot hold the lanes.
  staging = tmp_path / "staging"
  _graph(staging, {"projects": [f"fact-{i}" for i in range(60)]})
  prompts = []
  def answer(choice, prompt, **kwargs):
    prompts.append(prompt)
    return TextResult(json.dumps({"request_note_bodies": [f"notes/fact-{len(prompts)}.md"]}))
  monkeypatch.setattr(memory_runner, "run_text", answer)
  outcome = memory_runner._proposal(57, staging, [], [], memory_runner.ProviderPool([
    {"provider": "codex"}, {"provider": "claude"},
  ]))
  assert outcome.status == "degraded"
  assert len(prompts) == 2
  first, second = outcome.attempted_agents
  assert first["outcome"] == "context_requested"
  assert second["rejection_code"] == "note_body_request_limit"
  # A model answered, so the item is a recorded failure, not a deadline deferral.
  assert memory_runner._model_ran(outcome.attempted_agents) is True


def test_writer_prose_cannot_replace_root_contract_text(state, tmp_path):
  staging = tmp_path / "staging"
  graph = _graph(staging, {"projects": ["alpha"]})
  graph["nodes"].append({"id": "index", "path": "index.md", "type": "moc"})
  (staging / "graph.json").write_text(json.dumps(graph))
  root = ("---\ntype: moc\ntitle: Home\n---\n# Home\n\nSystem contract.\n"
          "- [[projects]] — Projects\n")
  (staging / "index.md").write_text(root)

  assert "index.md" not in memory_runner._consolidation_candidates(staging)
  assert memory_runner._editable_map(
    memory_runner._consolidation_item(staging, "index.md", {}),
  ) is None
  rewrite = _reviewed({"updates": [{
    "path": "index.md", "content": root.replace("System contract.", "Writer prose."),
  }]})
  for editable in (None, memory_runner._editable_map(
    memory_runner._consolidation_item(staging, "mocs/projects.md", {}),
  )):
    with pytest.raises(memory_runner.ProposalValidationError) as error:
      memory_runner._normalize_proposal(
        rewrite, allowed_chat_ids=set(), editable_map=editable,
      )
    assert error.value.code == "invalid_memory_file"
  assert (staging / "index.md").read_text() == root
