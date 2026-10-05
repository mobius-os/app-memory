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
    "---\ndescription: x\n---\n## Summary\nPlanning a trip to Kyoto.\n\n"
    "## Digest\nLong history.\n\n## Summary\nA recap heading inside the history.\n",
  )
  memory_store.append_capture("busy", "Prefers Thai food.")
  memory_store.append_capture("fresh", "Partner's cousin is called Maya.")
  memory_store.append_capture("busy", "Lives in Lisbon.")

  intake = memory_runner._collect_capture_intake()

  assert fetched == ["busy", "fresh"]
  assert [chat["id"] for chat in intake.chats] == ["busy", "fresh"]
  redacted = memory_runner._redacted_chat(intake.chats[0])
  assert "messages" not in redacted
  assert redacted["summary"] == "Planning a trip to Kyoto."
  assert redacted["captured_by_agent"] == [
    "Prefers Thai food.", "Lives in Lisbon.",
  ]
  assert intake.capture_count == 3


def test_a_note_without_a_short_summary_gives_no_context(state):
  (state / "chats" / "history-only").mkdir(parents=True)
  (state / "chats" / "history-only" / "index.md").write_text(
    "---\ndescription: x\n---\n## Summary\n\n## Digest\nLong history.\n\n"
    "## Summary\nA recap heading inside the history.\n",
  )
  assert memory_runner._chat_summary("history-only") == ""


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
  # The provisional row is a lookup and credits what the deep replay picked,
  # never overreach: an unresolved disagreement leans towards keeping a note.
  assert usage["notes/old.md"]["lookups_since_created"] == 3
  assert usage["notes/new.md"]["needed"] == 2
  assert usage["notes/new.md"]["lookups_since_created"] == 2
  assert usage["notes/new.md"]["last_needed_at"] == "2026-09-22T00:00:00+00:00"


def test_usage_evidence_uses_latest_judged_row_and_skips_torn_lines(
  monkeypatch, tmp_path,
):
  monkeypatch.setattr(memory_store, "STATE", tmp_path)
  monkeypatch.setattr(memory_store, "_note_creation_times", lambda: {})
  log = tmp_path / "recall-audit" / "x.jsonl"
  log.parent.mkdir()
  rows = [
    {"read_id": "one", "at": "2026-09-01T00:00:00+00:00",
     "verdict_source": "deep_replay_unreviewed", "live_selected": [],
     "missed_nodes": ["notes/a.md"]},
    {"read_id": "two", "at": "2026-09-01T00:00:00+00:00",
     "verdict_source": "writer_failed", "live_selected": [],
     "missed_nodes": ["notes/b.md"]},
    {"read_id": "one", "at": "2026-09-01T00:00:00+00:00",
     "verdict_source": "writer", "live_selected": ["notes/c.md"],
     "missed_nodes": []},
  ]
  torn = json.dumps({"read_id": "x", "reason": "café"}, ensure_ascii=False)
  torn = torn.encode("utf-8")[:torn.index("é") + 1] + b"\n"
  log.write_bytes(torn.join(
    (json.dumps(row, ensure_ascii=False) + "\n").encode() for row in rows
  ))

  usage = memory_store.note_usage_evidence()

  assert set(usage) == {"notes/c.md"}
  assert usage["notes/c.md"]["needed"] == 1


def test_usage_evidence_credits_deep_replay_picks_in_unreviewed_reads(
  monkeypatch, tmp_path,
):
  # A note live recall often misses, whose disagreements the writer never
  # reached, must not look unused just because those reads are unreviewed.
  monkeypatch.setattr(memory_store, "STATE", tmp_path)
  monkeypatch.setattr(memory_store, "_note_creation_times", lambda: {
    "notes/x.md": "2026-08-01T00:00:00+00:00",
  })
  log = tmp_path / "recall-audit" / "x.jsonl"
  log.parent.mkdir()
  rows = [
    {"read_id": f"u{i}", "at": f"2026-09-01T00:00:{i:02d}+00:00",
     "verdict_source": "deep_replay_unreviewed",
     "live_selected": ["notes/x.md"] if i < 3 else [],
     "deep_selected": ["notes/x.md"], "missed_nodes": [],
     "overselected_nodes": []}
    for i in range(8)
  ] + [
    {"read_id": f"a{i}", "at": f"2026-09-02T00:00:{i:02d}+00:00",
     "verdict_source": "deep_replay", "live_selected": ["notes/y.md"],
     "deep_selected": ["notes/y.md"], "missed_nodes": [],
     "overselected_nodes": []}
    for i in range(20)
  ]
  log.write_text("".join(json.dumps(row) + "\n" for row in rows))

  usage = memory_store.note_usage_evidence()

  # Live recall missed x in five of these reads; the replay's pick still
  # counts, as it did before audits were queued for review.
  assert usage["notes/x.md"]["needed"] == 8
  assert usage["notes/x.md"]["overreach"] == 0
  assert usage["notes/x.md"]["lookups_since_created"] == 28
  assert usage["notes/x.md"]["last_needed_at"] == "2026-09-01T00:00:07+00:00"


def test_latest_audit_rows_never_lets_a_provisional_row_replace_a_verdict(
  tmp_path,
):
  # A lost stats cursor can replay a read the writer already judged.
  log = tmp_path / "recall-audit" / "x.jsonl"
  log.parent.mkdir()
  rows = [
    {"read_id": "one", "verdict_source": "deep_replay_unreviewed"},
    {"read_id": "one", "verdict_source": "writer"},
    {"read_id": "one", "verdict_source": "deep_replay_unreviewed"},
    {"read_id": "two", "verdict_source": "deep_replay"},
    {"read_id": "two", "verdict_source": "writer_failed"},
    {"read_id": "three", "verdict_source": "deep_replay_unreviewed"},
    {"read_id": "three", "verdict_source": "writer_failed"},
  ]
  log.write_text("".join(json.dumps(row) + "\n" for row in rows))

  sources = {row["read_id"]: row["verdict_source"]
             for row in memory_store.latest_audit_rows(tmp_path)}

  assert sources == {"one": "writer", "two": "deep_replay",
                     "three": "writer_failed"}


def test_captures_skip_a_torn_multibyte_line(state):
  kept = memory_store.append_capture("chat-1", "Prefers tea")
  torn = json.dumps({"id": "torn", "chat_id": "chat-1", "text": "naïve"},
                    ensure_ascii=False)
  torn = torn.encode("utf-8")[:torn.index("ï") + 1] + b"\n"
  state.joinpath("captures.jsonl").write_bytes(
    torn + state.joinpath("captures.jsonl").read_bytes(),
  )
  assert [item["id"] for item in memory_store.load_captures()] == [kept["id"]]
