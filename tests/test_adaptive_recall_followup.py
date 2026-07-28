from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import memory_search


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
  add("b", "notes/b.md", "The complete detailed answer.")
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


class AdaptiveRecallFollowupTests(unittest.TestCase):
  def test_prompt_requires_explicit_support_and_resumable_parent_batches(self):
    prompt = memory_search._navigator_prompt(
      "specific lifecycle failure",
      [],
      [],
      breadth=4,
      depth_limit=4,
      audit=False,
    )
    self.assertIn("explicitly support every material", prompt)
    self.assertIn("A parent can appear again", prompt)

  def test_parent_can_be_expanded_again_for_another_relevant_batch(self):
    bodies = _revision()
    actions = iter([
      {"finish": False, "expand": [{"from": "index", "nodes": ["a"]}]},
      {"finish": False, "expand": [{"from": "a", "nodes": ["b"]}]},
      {"finish": False, "expand": [{"from": "a", "nodes": ["a-two"]}]},
      {"finish": True, "expand": [], "selected": ["b", "a-two"]},
    ])
    with mock.patch.object(
      memory_search,
      "read_revision_file",
      side_effect=lambda _commit, path: bodies[path],
    ):
      result = memory_search.traverse(
        "Both A details",
        "0" * 40,
        breadth=1,
        depth_limit=2,
        text_call=lambda _prompt: json.dumps(next(actions)),
      )

    self.assertEqual(
      [node.id for node in result.opened],
      ["index", "a", "b", "a-two"],
    )
    self.assertEqual(
      [item["expanded"] for item in result.decisions[:3]],
      [
        [{"from": "index", "nodes": ["a"]}],
        [{"from": "a", "nodes": ["b"]}],
        [{"from": "a", "nodes": ["a-two"]}],
      ],
    )

  def test_trace_records_unopened_frontier_when_navigator_stops(self):
    bodies = _revision()
    actions = iter([
      {"finish": False, "expand": [{"from": "index", "nodes": ["a"]}]},
      {"finish": False, "expand": [{"from": "a", "nodes": ["b"]}]},
      {"finish": True, "expand": [], "selected": ["b"]},
    ])
    with mock.patch.object(
      memory_search,
      "read_revision_file",
      side_effect=lambda _commit, path: bodies[path],
    ):
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
    self.assertIn("notes/a-two.md", paths)
    self.assertEqual(
      result.trace()["frontier_at_stop"],
      list(result.frontier_at_stop),
    )

  def test_live_reader_auto_fails_over_before_lexical_fallback(self):
    calls = []

    def run_text(provider, _prompt, **_kwargs):
      calls.append(provider)
      return None if provider == "claude" else '{"finish":true}'

    with (
      mock.patch.object(
        memory_search,
        "available_provider",
        return_value="claude",
      ),
      mock.patch.object(memory_search, "run_text", side_effect=run_text),
      mock.patch.dict(os.environ, {"MEMORY_READER_PROVIDER": "auto"}),
    ):
      text_call = memory_search._live_text_call()
      self.assertIsNotNone(text_call)
      self.assertEqual(text_call("navigate"), '{"finish":true}')

    self.assertEqual(calls, ["claude", "codex"])


if __name__ == "__main__":
  unittest.main()
