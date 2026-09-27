"""Replay benchmark: hindsight expectations and scoring stay honest."""

from __future__ import annotations

import json

import pytest

import memory_benchmark
from memory_search import RecallResult, RESULT_HIT


@pytest.fixture
def state(monkeypatch, tmp_path):
  monkeypatch.setattr(memory_benchmark, "STATE", tmp_path)
  monkeypatch.setattr(memory_benchmark, "BENCHMARK", tmp_path / "benchmark")
  monkeypatch.setattr(
    memory_benchmark, "REPLAY_SET", tmp_path / "benchmark" / "replay-set.json",
  )
  monkeypatch.setattr(memory_benchmark, "RUNS", tmp_path / "benchmark" / "runs")
  monkeypatch.setattr(
    memory_benchmark, "_current_note_paths",
    lambda: {"notes/a.md", "notes/b.md", "notes/c.md", "notes/noise.md"},
  )
  monkeypatch.setattr(
    memory_benchmark, "ready_pointer", lambda: {"commit": "c" * 40},
  )
  return tmp_path


def _write(path, rows):
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_expected_notes_are_hindsight_needs_not_raw_selection(state):
  _write(state / "read-log" / "d.jsonl", [
    {"read_id": "r1", "question": "Where does the partner live?"},
    {"read_id": "r2", "question": "Nothing useful here"},
    {"read_id": "r3", "question": "Mostly merged away"},
  ])
  _write(state / "recall-audit" / "d.jsonl", [
    {"read_id": "r1", "at": "2026-09-01", "outcome": "miss",
     "live_selected": ["notes/a.md", "notes/noise.md"],
     "overselected_nodes": ["notes/noise.md"],
     "missed_nodes": ["notes/b.md"]},
    {"read_id": "r2", "at": "2026-09-02", "outcome": "no_memory",
     "live_selected": [], "missed_nodes": []},
    {"read_id": "r3", "at": "2026-09-03", "outcome": "ok",
     "live_selected": ["notes/gone-1.md", "notes/gone-2.md", "notes/c.md"]},
  ])

  cases = memory_benchmark.freeze()["cases"]

  assert [case["read_id"] for case in cases] == ["r1"]
  assert cases[0]["expected"] == ["notes/a.md", "notes/b.md"]


def test_run_scores_recall_and_noise_against_expectations(monkeypatch, state):
  (state / "benchmark").mkdir()
  memory_benchmark.REPLAY_SET.write_text(json.dumps({
    "frozen_at": "t0",
    "cases": [{"read_id": "r1", "question": "q", "expected": [
      "notes/a.md", "notes/b.md", "notes/merged-since.md",
    ]}],
  }))
  monkeypatch.setitem(memory_benchmark.READERS, "walk", lambda _q: RecallResult(
    RESULT_HIT, "", files=("notes/a.md", "notes/noise.md"),
  ))

  record = memory_benchmark.run(parallel=1)

  result = record["results"][0]
  assert (result["expected"], result["found"]) == (2, 1)
  assert result["missed"] == ["notes/b.md"]
  assert result["extra"] == ["notes/noise.md"]
  assert record["summary"]["mean_recall"] == 0.5
  assert record["summary"]["noise"] == 0.5
  assert list(memory_benchmark.RUNS.glob("*.json"))


def test_single_pass_drops_invented_ids_and_falls_back_to_bm25(monkeypatch):
  import memory_search

  graph = {
    "nodes": [
      {"id": "index", "path": "index.md", "type": "moc", "title": "Home"},
      {"id": "lives-in-lisbon", "path": "notes/lives-in-lisbon.md",
       "type": "note", "title": "Partner lives in Lisbon", "description": ""},
      {"id": "likes-ramen", "path": "notes/likes-ramen.md", "type": "note",
       "title": "Partner loves ramen", "description": "Favourite dinner food."},
    ],
    "edges": [],
  }
  files = {
    "graph.json": json.dumps(graph), "index.md": "- [[lives-in-lisbon]]\n",
    "notes/lives-in-lisbon.md": "Lisbon.", "notes/likes-ramen.md": "Ramen.",
  }
  monkeypatch.setattr(memory_search, "read_revision_file", lambda _c, p: files[p])

  chosen = memory_search.select_in_one_pass(
    "book dinner", "c", guidance_record={},
    text_call=lambda _p: '{"selected":["likes-ramen","made-up"]}',
  )
  fallback = memory_search.select_in_one_pass(
    "ramen dinner", "c", guidance_record={}, text_call=lambda _p: "not json",
  )

  assert [node.id for node in chosen.selected] == ["likes-ramen"]
  assert fallback.decisions[0]["source"] == "lexical_fallback"
  assert fallback.selected[0].id == "likes-ramen"


def test_live_reader_follows_memory_settings_and_defaults_to_walk(
  monkeypatch, tmp_path,
):
  import memory_search

  monkeypatch.setattr(memory_search, "DATA_DIR", tmp_path)
  monkeypatch.delenv("APP_ID", raising=False)
  assert memory_search.live_reader() == "walk"

  monkeypatch.setenv("APP_ID", "57")
  settings = tmp_path / "apps" / "57" / "settings.json"
  settings.parent.mkdir(parents=True)
  settings.write_text(json.dumps({"live_reader": "single-pass"}))
  assert memory_search.live_reader() == "single-pass"

  settings.write_text(json.dumps({"live_reader": "telepathy"}))
  assert memory_search.live_reader() == "walk"


def test_deep_batch_maps_numbered_answers_and_drops_invented_or_map_ids(
  monkeypatch,
):
  import memory_search

  graph = {
    "nodes": [
      {"id": "index", "path": "index.md", "type": "moc", "title": "Home"},
      {"id": "likes-ramen", "path": "notes/likes-ramen.md", "type": "note",
       "title": "Partner loves ramen", "description": "Favourite dinner."},
    ],
    "edges": [],
  }
  monkeypatch.setattr(
    memory_search, "read_revision_file", lambda _c, _p: json.dumps(graph),
  )
  prompts = []
  monkeypatch.setattr(memory_search, "_live_text_call", lambda: (
    lambda prompt: prompts.append(prompt) or memory_search.NavigatorCall(
      '{"results":[{"n":1,"selected":["likes-ramen","index","made-up"]}]}',
    )
  ))

  results, _attempts = memory_search.deep_replay_batch(["dinner?", "q2"], "c")

  assert results == [{"selected": ["notes/likes-ramen.md"], "reason": ""}, None]
  assert prompts[0].count("Partner loves ramen") == 1
  assert "[1] dinner?" in prompts[0] and "[2] q2" in prompts[0]
