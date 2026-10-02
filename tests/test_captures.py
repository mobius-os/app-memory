"""In-chat captures: saved mid-chat, reconciled first by the nightly writer."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

import memory_runner
import memory_store

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def state(monkeypatch, tmp_path):
  monkeypatch.setattr(memory_store, "STATE", tmp_path)
  monkeypatch.setattr(memory_store, "CAPTURES", tmp_path / "captures.jsonl")
  monkeypatch.setattr(memory_runner, "_CHAT_NOTES", tmp_path / "chats")
  return tmp_path


def _chat_api(monkeypatch, gone=frozenset(), failing=frozenset()):
  fetched = []

  def api(path):
    chat_id = urllib.parse.unquote(
      urllib.parse.urlsplit(path).path.rsplit("/", 1)[-1],
    )
    fetched.append(chat_id)
    if chat_id in gone:
      return memory_runner.ApiResult(None, 404, "http_error")
    if chat_id in failing:
      return memory_runner.ApiResult(None, 503, "http_error")
    return memory_runner.ApiResult({
      "id": chat_id, "title": f"Chat {chat_id}",
      "updated_at": "2026-09-26T00:00:00", "deleted_at": None,
      "messages": [{"role": "user", "text": "the whole transcript"}],
    }, 200)
  monkeypatch.setattr(memory_runner, "_api_result", api)
  return fetched


def test_capture_rejects_missing_fact_or_chat(state):
  with pytest.raises(ValueError):
    memory_store.append_capture("chat-1", "   ")
  with pytest.raises(ValueError):
    memory_store.append_capture("", "Lives in Lisbon.")
  with pytest.raises(ValueError):
    memory_store.append_capture("chat/../x", "Lives in Lisbon.")
  assert memory_store.load_captures() == []


def test_only_chats_with_saved_facts_are_read_and_never_their_transcript(
  monkeypatch, state,
):
  fetched = _chat_api(monkeypatch)
  (state / "chats" / "busy").mkdir(parents=True)
  (state / "chats" / "busy" / "index.md").write_text(
    "---\ndescription: x\n---\n## Digest\nPlanning a trip to Kyoto.\n\n"
    "## Summary\nLong history.\n",
  )
  memory_store.append_capture("busy", "Prefers Thai food.")
  memory_store.append_capture("fresh", "Partner's cousin is called Maya.")
  memory_store.append_capture("busy", "Lives in Lisbon.")

  intake = memory_runner._collect_capture_intake()

  assert fetched == ["busy", "fresh"]
  assert [chat["id"] for chat in intake.chats] == ["busy", "fresh"]
  redacted = memory_runner._redacted_chat(intake.chats[0])
  assert "messages" not in redacted
  assert redacted["digest"] == "Planning a trip to Kyoto."
  assert redacted["captured_by_agent"] == [
    "Prefers Thai food.", "Lives in Lisbon.",
  ]
  assert intake.capture_count == 3


def test_only_captures_offered_to_the_writer_are_consumed(monkeypatch, state):
  _chat_api(monkeypatch)
  memory_store.append_capture("chat-1", "Lives in Lisbon.")
  intake = memory_runner._collect_capture_intake()
  # Saved after the nightly run read its intake: must wait for the next night.
  late = memory_store.append_capture("chat-1", "Moved to Porto.")

  memory_store.consume_captures({
    capture["id"]
    for chat in intake.chats
    for capture in chat.get("captures") or ()
  })

  assert [c["id"] for c in memory_store.load_captures()] == [late["id"]]


def test_purged_chat_drops_captures_but_outage_keeps_them(monkeypatch, state):
  _chat_api(monkeypatch, gone={"gone"}, failing={"flaky"})
  memory_store.append_capture("gone", "Unverifiable now.")
  kept = memory_store.append_capture("flaky", "Retry tomorrow.")

  intake = memory_runner._collect_capture_intake()

  assert intake.chats == []
  assert len(intake.dropped_ids) == 1
  assert intake.unreachable_chat_count == 1
  assert [c["id"] for c in memory_store.load_captures()] == [kept["id"]]


def test_remember_command_saves_and_reports_an_activity_receipt(tmp_path):
  result = subprocess.run(
    [sys.executable, str(REPO / "remember.py"), "Lives in Lisbon.", "chat-1"],
    capture_output=True, text=True, env={"DATA_DIR": str(tmp_path)},
  )

  assert result.returncode == 0, result.stderr
  receipt = next(
    line for line in result.stdout.splitlines()
    if line.startswith("MOBIUS_APP_ACTIVITY_V1:")
  )
  payload = json.loads(receipt.split(":", 1)[1])
  assert payload["activity_id"] == "memory-capture"
  assert payload["status"] == "succeeded"
  saved = (tmp_path / "shared/memory/app-state/captures.jsonl").read_text()
  assert json.loads(saved)["text"] == "Lives in Lisbon."


def test_usage_evidence_counts_needed_overreach_and_lookups_since_creation(
  monkeypatch, tmp_path,
):
  monkeypatch.setattr(memory_store, "STATE", tmp_path)
  monkeypatch.setattr(memory_store, "_note_creation_times", lambda: {
    "notes/old.md": "2026-08-01T00:00:00+00:00",
    "notes/new.md": "2026-09-20T00:00:00+00:00",
  })
  log = tmp_path / "recall-audit" / "x.jsonl"
  log.parent.mkdir()
  rows = [
    {"at": "2026-08-02T00:00:00+00:00", "live_selected": ["notes/old.md"],
     "overselected_nodes": ["notes/old.md"], "missed_nodes": []},
    {"at": "2026-09-21T00:00:00+00:00", "live_selected": [],
     "missed_nodes": ["notes/new.md"]},
    {"at": "2026-09-22T00:00:00+00:00", "live_selected": ["notes/old.md"],
     "overselected_nodes": [], "missed_nodes": [],
     "verdict_source": "deep_replay_unreviewed",
     "deep_selected": ["notes/new.md"]},
  ]
  log.write_text("".join(json.dumps(row) + "\n" for row in rows))

  usage = memory_store.note_usage_evidence()

  assert usage["notes/old.md"]["needed"] == 0
  assert usage["notes/old.md"]["overreach"] == 1
  assert usage["notes/old.md"]["lookups_since_created"] == 2
  assert usage["notes/new.md"]["needed"] == 1
  assert usage["notes/new.md"]["lookups_since_created"] == 1
  assert usage["notes/new.md"]["last_needed_at"] == "2026-09-21T00:00:00+00:00"
