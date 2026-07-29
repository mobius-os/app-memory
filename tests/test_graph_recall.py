from __future__ import annotations

import json

import memory_search
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
  assert "Never return a near-neighbor" in prompt
  assert "`selected` MUST be empty" in prompt
  assert "Confirmed absence is success" in prompt
  assert "IDs mentioned only in `reason` are not expansions" in prompt


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
  assert logged["question"] == "Which detailed fact matters?"
  assert logged["files"] == ["notes/b.md"]
  assert logged["traversal"]["opened"][1]["path"] == "mocs/a.md"
  assert logged["traversal"]["selected"] == ["notes/b.md"]


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
  monkeypatch.setattr(memory_search, "_live_policy", lambda: (4, 4))

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
