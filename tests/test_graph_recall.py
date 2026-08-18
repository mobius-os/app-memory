from __future__ import annotations

import json

import memory_search
from memory_text_provider import ProviderFailure, TextResult
import memory_store


def _revision():
  nodes = []
  bodies = {}

  def add(node_id: str, path: str, body: str):
    nodes.append({
      "id": node_id,
      "path": path,
      "title": node_id.upper(),
      "description": f"route to {node_id}",
      "type": "moc" if path.startswith(("index", "mocs/")) else "note",
    })
    bodies[path] = body

  add("index", "index.md", "- [[a]]\n- [[c]]\n- [[unused]]")
  add("a", "mocs/a.md", "- [[b]]\n- [[a-two]]")
  add("c", "mocs/c.md", "- [[c-one]]\n- [[c-two]]")
  add("unused", "mocs/unused.md", "Nothing")
  detail = "The complete detailed answer.\n" + ("full memory text " * 200)
  add("b", "notes/b.md", detail)
  add("a-two", "notes/a-two.md", "Second A detail")
  add("c-one", "notes/c-one.md", "First C detail")
  add("c-two", "notes/c-two.md", "Second C detail")
  graph = {
    "nodes": nodes,
    "edges": [
      {"kind": "link", "source": "index", "target": "a"},
      {"kind": "link", "source": "index", "target": "c"},
      {"kind": "link", "source": "index", "target": "unused"},
      {"kind": "link", "source": "a", "target": "b"},
      {"kind": "link", "source": "a", "target": "a-two"},
      {"kind": "link", "source": "c", "target": "c-one"},
      {"kind": "link", "source": "c", "target": "c-two"},
    ],
  }
  bodies["graph.json"] = json.dumps(graph)
  return bodies


def test_traversal_opens_per_parent_and_returns_only_selected_full_node(monkeypatch):
  bodies = _revision()
  monkeypatch.setattr(
    memory_search,
    "read_revision_file",
    lambda _commit, path: bodies[path],
  )
  actions = iter([
    # Breadth two is enforced for index even though the navigator requests 3.
    {"finish": False, "expand": [{
      "from": "index", "nodes": ["a", "c", "unused"],
    }], "selected": []},
    # Breadth is per open parent, not a global node budget.
    {"finish": False, "expand": [
      {"from": "a", "nodes": ["b", "a-two"]},
      {"from": "c", "nodes": ["c-one", "c-two"]},
    ], "selected": []},
    {"finish": True, "expand": [], "selected": ["b"]},
  ])

  result = memory_search.traverse(
    "What is the complete detailed answer?",
    "0" * 40,
    breadth=2,
    depth_limit=2,
    text_call=lambda _prompt: json.dumps(next(actions)),
  )

  assert [node.id for node in result.opened] == [
    "index", "a", "c", "b", "a-two", "c-one", "c-two",
  ]
  assert [node.id for node in result.selected] == ["b"]
  assert len(result.opened) > result.breadth
  assert [decision["round"] for decision in result.decisions] == [1, 2, 3]
  recall = memory_search._answer(result)
  assert recall.files == ("notes/b.md",)
  assert bodies["notes/b.md"].rstrip() in recall.answer
  assert len(bodies["notes/b.md"]) > 900


def test_direct_live_selector_uses_one_call_and_loads_only_selected_bodies(
  monkeypatch,
):
  bodies = _revision()
  reads = []

  def read(_commit, path):
    reads.append(path)
    return bodies[path]

  monkeypatch.setattr(memory_search, "read_revision_file", read)
  prompts = []

  def select(prompt):
    prompts.append(prompt)
    return json.dumps({"selected": ["b"], "reason": "Exact answer note."})

  result = memory_search.direct_live_traverse(
    "What is the complete detailed answer?",
    "0" * 40,
    depth_limit=4,
    text_call=select,
  )

  assert len(prompts) == 1
  assert result.rounds == 1
  assert result.stop_reason == "direct_catalog_selection"
  assert [node.id for node in result.selected] == ["b"]
  assert reads == ["graph.json", "index.md", "notes/b.md"]
  assert [node.id for node in result.opened] == ["index", "b"]
  assert any(
    node["id"] == "a"
    for parent in result.frontier_at_stop
    for node in parent["nodes"]
  )
  assert result.decisions[0]["catalog_nodes"] == 8


def test_direct_live_selector_fallback_prefers_deepest_lexical_match(monkeypatch):
  bodies = _revision()
  monkeypatch.setattr(
    memory_search, "read_revision_file", lambda _commit, path: bodies[path],
  )

  result = memory_search.direct_live_traverse(
    "route a-two",
    "0" * 40,
    depth_limit=4,
    text_call=lambda _prompt: "not json",
  )

  assert [node.id for node in result.selected] == ["a-two"]
  assert result.decisions[0]["source"] == "lexical_fallback"


def test_direct_live_catalog_excludes_unreachable_nodes(monkeypatch):
  bodies = _revision()
  graph = json.loads(bodies["graph.json"])
  graph["nodes"].append({
    "id": "orphan", "path": "notes/orphan.md", "title": "Secret answer",
    "description": "Must not be exposed", "type": "note",
  })
  bodies["graph.json"] = json.dumps(graph)
  bodies["notes/orphan.md"] = "unreachable"
  monkeypatch.setattr(
    memory_search, "read_revision_file", lambda _commit, path: bodies[path],
  )
  prompt = []

  result = memory_search.direct_live_traverse(
    "secret answer", "0" * 40, depth_limit=4,
    text_call=lambda value: prompt.append(value) or json.dumps({
      "selected": ["orphan"],
    }),
  )

  assert "orphan" not in prompt[0]
  assert result.selected == ()


def test_direct_live_selector_bounds_prompt_and_keeps_late_matches_discoverable(
  monkeypatch,
):
  graph = {
    "nodes": [{
      "id": "index", "path": "index.md", "title": "Index",
      "description": "root", "type": "map",
    }],
    "edges": [{"kind": "link", "source": "index", "target": f"note-{index}"}
              for index in range(200)],
  }
  graph["nodes"].extend({
    "id": f"note-{index}", "path": f"notes/{index}.md",
    "title": f"Note {index}",
    "description": ("late needle" if index == 199 else "x" * 800),
    "type": "note",
  } for index in range(200))

  bodies = {
    "graph.json": json.dumps(graph),
    "index.md": "# Index\n",
    "notes/199.md": "The durable late answer.\n",
  }
  monkeypatch.setattr(
    memory_search, "read_revision_file", lambda _commit, path: bodies[path],
  )
  prompts = []

  result = memory_search.direct_live_traverse(
    "Where is the late needle?", "0" * 40, depth_limit=4,
    text_call=lambda prompt: prompts.append(prompt) or json.dumps({
      "selected": ["note-199"],
    }),
  )

  assert len(prompts[0].encode("utf-8")) <= memory_search.MAX_SELECTOR_PROMPT_BYTES
  assert '"id": "note-199"' in prompts[0]
  assert '"id": "note-198"' not in prompts[0]
  assert result.decisions[0]["catalog_nodes"] == 201
  assert result.decisions[0]["selector_nodes"] < 201
  assert [node.id for node in result.selected] == ["note-199"]


def test_navigator_can_select_nodes_opened_at_the_depth_limit(monkeypatch):
  bodies = _revision()
  monkeypatch.setattr(
    memory_search,
    "read_revision_file",
    lambda _commit, path: bodies[path],
  )
  actions = iter([
    {
      "finish": False,
      "expand": [{"from": "index", "nodes": ["a"]}],
      "selected": [],
    },
    {"finish": True, "expand": [], "selected": ["a"]},
  ])
  result = memory_search.traverse(
    "route a",
    "0" * 40,
    breadth=1,
    depth_limit=1,
    text_call=lambda _prompt: json.dumps(next(actions)),
  )
  assert [node.id for node in result.opened] == ["index", "a"]
  assert [node.id for node in result.selected] == ["a"]
  assert result.rounds == 2


def test_navigator_prompt_forbids_adjacent_nodes_as_empty_padding():
  prompt = memory_search._navigator_prompt(
    "specific lifecycle failure",
    [],
    [],
    breadth=4,
    depth_limit=4,
    audit=False,
  )
  assert "specific claim or predicate" in prompt
  assert "explicitly support every material" in prompt
  assert "Never return a near-neighbor" in prompt
  assert "`selected` MUST be empty" in prompt
  assert "Confirmed absence is success" in prompt
  assert "IDs mentioned only in `reason` are not expansions" in prompt
  assert "a parent is not offered again" in prompt


def test_navigator_prompt_carries_full_content_only_for_active_or_selected_nodes():
  opened = [
    memory_search.OpenedNode(
      "index", "index.md", "Root", "Root route", "moc", 0, None,
      "complete root body",
    ),
    memory_search.OpenedNode(
      "topic", "mocs/topic.md", "Topic", "Topic route", "moc", 1,
      "index", "complete topic body",
    ),
    memory_search.OpenedNode(
      "answer", "notes/answer.md", "Answer", "Answer route", "note", 2,
      "topic", "complete answer body",
    ),
  ]
  prompt = memory_search._navigator_prompt(
    "What matters?", opened, [], breadth=4, depth_limit=4, audit=False,
    active_ids={"answer"}, selected_ids={"topic"},
  )
  state = json.loads(prompt.split("STATE:\n", 1)[1])
  by_id = {item["id"]: item for item in state["opened"]}

  assert "content" not in by_id["index"]
  assert by_id["topic"]["content"] == "complete topic body"
  assert by_id["answer"]["content"] == "complete answer body"


def test_guided_expansion_prunes_unselected_siblings_instead_of_revisiting_parent(
  monkeypatch,
):
  bodies = _revision()
  monkeypatch.setattr(
    memory_search,
    "read_revision_file",
    lambda _commit, path: bodies[path],
  )
  actions = iter([
    {"finish": False, "expand": [{"from": "index", "nodes": ["a"]}]},
    {"finish": False, "expand": [{"from": "a", "nodes": ["b"]}]},
    {"finish": True, "expand": [], "selected": ["b"]},
  ])

  result = memory_search.traverse(
    "The first A detail",
    "0" * 40,
    breadth=1,
    depth_limit=2,
    text_call=lambda _prompt: json.dumps(next(actions)),
  )

  assert [node.id for node in result.opened] == ["index", "a", "b"]
  assert [item["expanded"] for item in result.decisions[:2]] == [
    [{"from": "index", "nodes": ["a"]}],
    [{"from": "a", "nodes": ["b"]}],
  ]
  assert result.decisions[2]["active"] == ["b"]
  assert any(
    node["path"] == "notes/a-two.md"
    for parent in result.frontier_at_stop
    for node in parent["nodes"]
  )


def test_live_round_limit_turns_last_decision_into_selection_only(monkeypatch):
  bodies = _revision()
  monkeypatch.setattr(
    memory_search,
    "read_revision_file",
    lambda _commit, path: bodies[path],
  )
  prompts = []
  actions = iter([
    {"finish": False, "expand": [{"from": "index", "nodes": ["a"]}]},
    {"finish": False, "expand": [{"from": "a", "nodes": ["b"]}]},
  ])

  def decide(prompt):
    prompts.append(prompt)
    return json.dumps(next(actions))

  result = memory_search.traverse(
    "The complete detailed answer",
    "0" * 40,
    breadth=1,
    depth_limit=4,
    round_limit=2,
    text_call=decide,
  )

  assert result.rounds == 2
  assert result.stop_reason == "round_limit"
  assert [node.id for node in result.opened] == ["index", "a"]
  assert '"expandable": []' in prompts[-1]
  assert "final navigation decision" in prompts[-1]
  assert result.trace()["round_limit"] == 2


def test_trace_records_unopened_frontier_when_navigator_stops(monkeypatch):
  bodies = _revision()
  monkeypatch.setattr(
    memory_search,
    "read_revision_file",
    lambda _commit, path: bodies[path],
  )
  actions = iter([
    {"finish": False, "expand": [{"from": "index", "nodes": ["a"]}]},
    {"finish": False, "expand": [{"from": "a", "nodes": ["b"]}]},
    {"finish": True, "expand": [], "selected": ["b"]},
  ])

  result = memory_search.traverse(
    "The complete detailed answer",
    "0" * 40,
    breadth=1,
    depth_limit=2,
    text_call=lambda _prompt: json.dumps(next(actions)),
  )

  paths = {
    node["path"]
    for parent in result.frontier_at_stop
    for node in parent["nodes"]
  }
  assert "notes/a-two.md" in paths
  assert result.trace()["frontier_at_stop"] == list(result.frontier_at_stop)


def test_valid_model_no_progress_preserves_intentional_empty_selection(monkeypatch):
  bodies = _revision()
  monkeypatch.setattr(
    memory_search,
    "read_revision_file",
    lambda _commit, path: bodies[path],
  )

  result = memory_search.traverse(
    "Is there a durable fact about a submarine?",
    "0" * 40,
    breadth=4,
    depth_limit=4,
    text_call=lambda _prompt: json.dumps({
      "finish": False,
      "expand": [],
      "selected": [],
      "reason": "No opened node states this and no relevant child remains.",
    }),
  )

  assert result.stop_reason == "navigator_made_no_progress"
  assert result.selected == ()
  assert result.decisions[-1]["source"] == "model"


def test_invalid_model_output_still_uses_lexical_fallback(monkeypatch):
  bodies = _revision()
  monkeypatch.setattr(
    memory_search,
    "read_revision_file",
    lambda _commit, path: bodies[path],
  )

  result = memory_search.traverse(
    "route a",
    "0" * 40,
    breadth=1,
    depth_limit=1,
    text_call=lambda _prompt: "not json",
  )

  assert result.selected
  assert result.decisions[-1]["source"] == "lexical_fallback"


def test_live_reader_auto_fails_over_before_lexical_fallback(monkeypatch):
  calls = []
  monkeypatch.setattr(memory_search, "available_provider", lambda _requested: "claude")
  monkeypatch.setattr(
    memory_search, "_live_capacity", lambda providers: (providers, []),
  )
  monkeypatch.setattr(
    memory_search,
    "run_text",
    lambda provider, _prompt, **_kwargs: (
      calls.append(provider)
      or (
        TextResult(None, ProviderFailure("timeout"))
        if provider == "claude"
        else TextResult('{"finish":true}')
      )
    ),
  )
  monkeypatch.setenv("MEMORY_READER_PROVIDER", "auto")

  text_call = memory_search._live_text_call()

  assert text_call is not None
  result = text_call("navigate")
  assert result.text == '{"finish":true}'
  assert [attempt["outcome"] for attempt in result.attempts] == [
    "timeout", "ok",
  ]
  assert all(attempt["elapsed_ms"] >= 0 for attempt in result.attempts)
  assert calls == ["claude", "codex"]


def test_live_reader_remembers_terminal_provider_failure(monkeypatch):
  calls = []
  monkeypatch.setattr(memory_search, "available_provider", lambda _requested: "claude")
  monkeypatch.setattr(
    memory_search, "_live_capacity", lambda providers: (providers, []),
  )

  def run(provider, _prompt, **_kwargs):
    calls.append(provider)
    if provider == "claude":
      return TextResult(
        None, ProviderFailure("usage_limit", terminal=True, scope="provider"),
      )
    return TextResult('{"finish":true}')

  monkeypatch.setattr(memory_search, "run_text", run)
  monkeypatch.setenv("MEMORY_READER_PROVIDER", "auto")
  text_call = memory_search._live_text_call()

  assert text_call is not None
  first = text_call("first")
  second = text_call("second")
  assert first.text == second.text == '{"finish":true}'
  assert first.attempts[0]["outcome"] == "usage_limit"
  assert second.attempts[0]["skipped"] is True
  assert second.attempts[0]["outcome"] == "usage_limit"
  assert calls == ["claude", "codex", "codex"]


def test_live_reader_skips_provider_exhausted_in_fresh_usage_snapshot(monkeypatch):
  calls = []
  monkeypatch.setattr(memory_search, "available_provider", lambda _requested: "claude")
  monkeypatch.setattr(
    memory_search,
    "_live_capacity",
    lambda providers: (
      [provider for provider in providers if provider != "claude"],
      [{
        "provider": "claude",
        "outcome": "usage_snapshot_exhausted",
        "skipped": True,
        "elapsed_ms": 21,
      }],
    ),
  )
  monkeypatch.setattr(
    memory_search,
    "run_text",
    lambda provider, _prompt, **_kwargs: (
      calls.append(provider) or TextResult('{"finish":true}')
    ),
  )
  monkeypatch.setenv("MEMORY_READER_PROVIDER", "auto")

  text_call = memory_search._live_text_call()
  assert text_call is not None
  result = text_call("navigate")

  assert calls == ["codex"]
  assert result.attempts[0] == {
    "provider": "claude",
    "outcome": "usage_snapshot_exhausted",
    "skipped": True,
    "elapsed_ms": 21,
  }
  assert result.attempts[1]["provider"] == "codex"
  assert result.attempts[1]["outcome"] == "ok"


def test_live_usage_snapshot_is_fresh_for_each_recall(monkeypatch):
  checks = []
  monkeypatch.setattr(memory_search, "available_provider", lambda _requested: "codex")

  def capacity(providers):
    checks.append(list(providers))
    return providers, []

  monkeypatch.setattr(memory_search, "_live_capacity", capacity)
  monkeypatch.setattr(
    memory_search,
    "run_text",
    lambda _provider, _prompt, **_kwargs: TextResult('{"finish":true}'),
  )
  monkeypatch.setenv("MEMORY_READER_PROVIDER", "codex")

  first = memory_search._live_text_call()
  second = memory_search._live_text_call()
  assert first is not None and second is not None
  first("one")
  first("two")
  second("three")

  assert checks == [["codex"], ["codex"]]


def test_usage_snapshot_only_blocks_provider_wide_allowance_windows(monkeypatch):
  snapshots = iter([
    {
      "state": "ready",
      "windows": [{"id": "seven_day_opus", "used_percent": 100}],
    },
    {
      "state": "ready",
      "windows": [{"id": "seven_day", "used_percent": 100}],
    },
  ])

  class Response:
    def __enter__(self):
      return self

    def __exit__(self, *_args):
      return False

    def read(self):
      return json.dumps(next(snapshots)).encode()

  monkeypatch.setattr(
    memory_search.urllib.request,
    "urlopen",
    lambda *_args, **_kwargs: Response(),
  )
  monkeypatch.setenv("API_BASE_URL", "http://mobius.test")
  monkeypatch.setenv("AGENT_TOKEN", "owner-token")

  assert memory_search._provider_usage_state("claude")[0] == "ready"
  assert memory_search._provider_usage_state("claude")[0] == "exhausted"


def test_record_read_separates_opened_and_selected_and_keeps_replay_query(
  monkeypatch, tmp_path,
):
  monkeypatch.setattr(memory_store, "STATE", tmp_path / "app-state")
  traversal = {
    "breadth": 4,
    "depth_limit": 4,
    "opened": [
      {"id": "index", "path": "index.md", "depth": 0, "parent": None},
      {"id": "a", "path": "mocs/a.md", "depth": 1, "parent": "index"},
      {"id": "b", "path": "notes/b.md", "depth": 2, "parent": "a"},
    ],
    "selected": ["notes/b.md"],
  }
  memory_store.record_read(
    "0" * 40,
    "Which detailed fact matters?",
    ["notes/b.md"],
    "chat:unsafe/id",
    traversal=traversal,
  )

  latest = json.loads(next((tmp_path / "app-state" / "read-trace").glob("*.json")).read_text())
  logged = json.loads(next((tmp_path / "app-state" / "read-log").glob("*.jsonl")).read_text())
  assert latest == logged
  assert logged["schema"] == 3
  assert logged["status"] == "completed"
  assert logged["question"] == "Which detailed fact matters?"
  assert logged["files"] == ["notes/b.md"]
  assert logged["traversal"]["opened"][1]["path"] == "mocs/a.md"
  assert logged["traversal"]["selected"] == ["notes/b.md"]


def test_failed_read_is_observable_without_affecting_usage(monkeypatch, tmp_path):
  monkeypatch.setattr(memory_store, "STATE", tmp_path / "app-state")

  memory_store.record_read(
    None,
    "What should have been recalled?",
    [],
    "chat-1",
    status="failed",
    reason="not_ready",
  )

  trace = json.loads(
    (tmp_path / "app-state" / "read-trace" / "chat-1.json").read_text()
  )
  assert trace["status"] == "failed"
  assert trace["reason"] == "not_ready"
  assert trace["commit"] is None
  assert trace["files"] == []
  assert trace["traversal"] == {}
  assert not (tmp_path / "app-state" / "usage.json").exists()
  assert not (tmp_path / "app-state" / "read-log").exists()


def test_retrieve_distinguishes_not_ready_from_a_graph_read_failure(monkeypatch):
  monkeypatch.setattr(memory_search, "ready_pointer", lambda: None)
  not_ready = memory_search.retrieve("what did we decide")
  assert not_ready.status == memory_search.RESULT_FAILED
  assert not_ready.reason == memory_search.RESULT_REASON_NOT_READY

  monkeypatch.setattr(
    memory_search,
    "ready_pointer",
    lambda: {"commit": "0" * 40},
  )
  monkeypatch.setattr(memory_search, "_live_policy", lambda: 4)

  def fail_read(*_args, **_kwargs):
    raise OSError("private internal detail")

  monkeypatch.setattr(memory_search, "traverse", fail_read)
  read_failed = memory_search.retrieve("what did we decide")
  assert read_failed.status == memory_search.RESULT_FAILED
  assert read_failed.reason == memory_search.RESULT_REASON_READ_FAILED


def test_failed_receipt_exposes_only_a_safe_reason_enum():
  assert memory_search._result_payload(memory_search.RecallResult(
    memory_search.RESULT_FAILED,
    "Memory lookup failed.",
    reason=memory_search.RESULT_REASON_NOT_READY,
  )) == {
    "status": memory_search.RESULT_FAILED,
    "reason": memory_search.RESULT_REASON_NOT_READY,
  }
  assert memory_search._result_payload(memory_search.RecallResult(
    memory_search.RESULT_FAILED,
    "Memory lookup failed.",
    reason="/private/path",
  )) == {"status": memory_search.RESULT_FAILED}


def test_empty_receipt_is_an_explicit_no_relevant_result():
  assert memory_search._result_payload(memory_search.RecallResult(
    memory_search.RESULT_EMPTY,
    "No relevant memories.",
  )) == {
    "status": memory_search.RESULT_EMPTY,
    "reason": memory_search.RESULT_REASON_NO_RELEVANT_RESULT,
  }
