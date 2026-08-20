from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
from pathlib import Path

import pytest

import memory_graph
import memory_runner
from memory_text_provider import TextResult, classify_process_failure


MEMORY_CORE_PROMPT = " ".join(
  (Path(memory_runner.__file__).parent / "memory-core.md").read_text().split()
)


def _self_review():
  return {
    "hardest_decision": "Distinguishing durable facts from transient context.",
    "possibly_missed": "none",
    "prompt_change": "none",
    "next_experiment": "none",
  }


def _memory_contract():
  return {
    "schema": 5,
    "data": {"shared_memory": "write"},
    "background": {
      "job": "fetch.sh",
      "mode": "scheduled",
    },
  }


def test_app_active_requires_current_memory_permissions_and_scheduled_job(
  monkeypatch,
):
  app = {
    "id": 57,
    "system_app": True,
    "capability_contract": _memory_contract(),
  }
  monkeypatch.setattr(memory_runner, "_api_json", lambda _path: app)
  assert memory_runner._app_active(57) is True

  future_additive_contract = {
    **app,
    "capability_contract": {**_memory_contract(), "schema": 6},
  }
  monkeypatch.setattr(
    memory_runner, "_api_json", lambda _path: future_additive_contract,
  )
  assert memory_runner._app_active(57) is True

  legacy_receipt = {
    **app,
    "capability_contract": {
      **_memory_contract(),
      "schema": 2,
      "background": {
        "job": "fetch.sh",
        "mode": "scheduled",
        "agent": True,
        "authority": "scoped_system_job",
      },
    },
  }
  monkeypatch.setattr(memory_runner, "_api_json", lambda _path: legacy_receipt)
  assert memory_runner._app_active(57) is False

  wrong_permission = {
    **app,
    "capability_contract": {
      **_memory_contract(),
      "data": {"shared_memory": "read"},
    },
  }
  monkeypatch.setattr(memory_runner, "_api_json", lambda _path: wrong_permission)
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
  assert recorded[0]["error_code"] == "missing_app_token"
  assert recorded[0]["commit"] == "ready-commit"
  assert recorded[0]["run_id"].startswith("preflight-")


def test_orphaned_running_status_is_closed_before_the_next_run(
  monkeypatch, tmp_path,
):
  state = tmp_path / "app-state"
  state.mkdir()
  (state / "run-status.json").write_text(json.dumps({
    "schema": 1,
    "run_id": "orphaned-run",
    "status": "running",
    "started_at": "2026-07-29T05:30:00+00:00",
    "commit": "ready-before",
  }))
  recorded = []
  monkeypatch.setattr(memory_runner, "STATE", state)
  monkeypatch.setattr(
    memory_runner, "_record_run_status", lambda item: recorded.append(item),
  )

  memory_runner._reconcile_interrupted_run("2026-07-30T05:30:00+00:00")

  assert recorded == [{
    "schema": 1,
    "run_id": "orphaned-run",
    "status": "abandoned",
    "started_at": "2026-07-29T05:30:00+00:00",
    "finished_at": "2026-07-30T05:30:00+00:00",
    "commit": "ready-before",
    "error_code": "previous_run_interrupted",
  }]


def test_chat_discovery_pages_to_the_durable_marker(monkeypatch, tmp_path):
  state = tmp_path / "app-state"
  pending = state / "pending-chat-ids.json"
  discovery = state / "chat-discovery.json"
  state.mkdir()
  pending.write_text(json.dumps({"schema": 1, "chat_ids": ["backlog"]}))
  discovery.write_text(json.dumps({
    "schema": 1,
    "newest": {"recency_at": "2026-07-28T01:00:00", "id": "seen"},
  }))
  monkeypatch.setattr(memory_runner, "_PENDING_CHAT_IDS", pending)
  monkeypatch.setattr(memory_runner, "_CHAT_DISCOVERY", discovery)
  calls = []

  def api(path):
    calls.append(path)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
    if "before_id" not in query:
      return memory_runner.ApiResult({
        "items": [
          {"id": "new-2", "recency_at": "2026-07-30T02:00:00"},
          {"id": "new-1", "recency_at": "2026-07-29T02:00:00"},
        ],
        "next_before": {
          "recency_at": "2026-07-29T02:00:00", "id": "new-1",
        },
      }, 200)
    return memory_runner.ApiResult({
      "items": [
        {"id": "seen", "recency_at": "2026-07-28T01:00:00"},
        {"id": "older", "recency_at": "2026-07-27T01:00:00"},
      ],
      "next_before": None,
    }, 200)

  monkeypatch.setattr(memory_runner, "_api_result", api)

  ids, complete, queue_ok = memory_runner._discover_chat_ids()

  assert ids == ["new-1", "new-2"]
  assert complete is queue_ok is True
  assert len(calls) == 2
  assert all(
    urllib.parse.parse_qs(urllib.parse.urlsplit(call).query).get(
      "include_deleted"
    ) == ["true"]
    for call in calls
  )
  assert memory_runner._load_pending_chat_ids() == [
    "backlog", "new-1", "new-2",
  ]
  assert json.loads(discovery.read_text())["newest"] == {
    "recency_at": "2026-07-30T02:00:00", "id": "new-2",
  }


def test_fresh_discovery_queues_descending_pages_oldest_first(monkeypatch, tmp_path):
  state = tmp_path / "app-state"
  state.mkdir()
  pending = state / "pending.json"
  monkeypatch.setattr(memory_runner, "_PENDING_CHAT_IDS", pending)
  monkeypatch.setattr(memory_runner, "_CHAT_DISCOVERY", state / "discovery.json")

  def api(path):
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
    if "before_id" not in query:
      return memory_runner.ApiResult({
        "items": [
          {"id": "newest", "recency_at": "2026-07-30T03:00:00"},
          {"id": "middle", "recency_at": "2026-07-30T02:00:00"},
        ],
        "next_before": {
          "recency_at": "2026-07-30T02:00:00", "id": "middle",
        },
      }, 200)
    return memory_runner.ApiResult({
      "items": [
        {"id": "oldest", "recency_at": "2026-07-30T01:00:00"},
      ],
      "next_before": None,
    }, 200)

  monkeypatch.setattr(memory_runner, "_api_result", api)

  ids, complete, queue_ok = memory_runner._discover_chat_ids()

  assert complete is queue_ok is True
  assert ids == ["oldest", "middle", "newest"]
  assert memory_runner._load_pending_chat_ids() == ids


def test_chat_discovery_uses_ordered_watermark_and_skips_empty_rows(
  monkeypatch, tmp_path,
):
  state = tmp_path / "app-state"
  pending = state / "pending-chat-ids.json"
  discovery = state / "chat-discovery.json"
  state.mkdir()
  discovery.write_text(json.dumps({
    "schema": 1,
    "newest": {"recency_at": "2026-07-28T01:00:00", "id": "moved"},
  }))
  monkeypatch.setattr(memory_runner, "_PENDING_CHAT_IDS", pending)
  monkeypatch.setattr(memory_runner, "_CHAT_DISCOVERY", discovery)
  calls = []

  def api(path):
    calls.append(path)
    return memory_runner.ApiResult({
      "items": [
        {
          "id": "moved", "recency_at": "2026-07-30T03:00:00",
          "message_count": 2,
        },
        {
          "id": "empty", "recency_at": "2026-07-30T02:00:00",
          "message_count": 0,
        },
        {
          "id": "new", "recency_at": "2026-07-29T01:00:00",
          "message_count": 1,
        },
        {
          "id": "older", "recency_at": "2026-07-27T01:00:00",
          "message_count": 4,
        },
      ],
      "next_before": None,
    }, 200)

  monkeypatch.setattr(memory_runner, "_api_result", api)

  ids, complete, queue_ok = memory_runner._discover_chat_ids()

  assert ids == ["new", "moved"]
  assert complete is queue_ok is True
  assert len(calls) == 1
  assert memory_runner._load_pending_chat_ids() == ["new", "moved"]
  assert json.loads(discovery.read_text())["newest"] == {
    "recency_at": "2026-07-30T03:00:00", "id": "moved",
  }


def test_chat_discovery_stops_at_watermark_after_marker_chat_disappears(
  monkeypatch, tmp_path,
):
  state = tmp_path / "app-state"
  state.mkdir()
  monkeypatch.setattr(memory_runner, "_PENDING_CHAT_IDS", state / "pending.json")
  marker = state / "discovery.json"
  marker.write_text(json.dumps({
    "schema": 1,
    "newest": {"recency_at": "2026-07-28T01:00:00", "id": "gone"},
  }))
  monkeypatch.setattr(memory_runner, "_CHAT_DISCOVERY", marker)
  monkeypatch.setattr(
    memory_runner,
    "_api_result",
    lambda _path: memory_runner.ApiResult({
      "items": [
        {
          "id": "new", "recency_at": "2026-07-29T01:00:00",
          "message_count": 1,
        },
        {
          "id": "older", "recency_at": "2026-07-27T01:00:00",
          "message_count": 1,
        },
      ],
      "next_before": None,
    }, 200),
  )

  ids, complete, queue_ok = memory_runner._discover_chat_ids()

  assert ids == ["new"]
  assert complete is queue_ok is True


def test_all_empty_discovery_page_advances_watermark_without_queueing(
  monkeypatch, tmp_path,
):
  state = tmp_path / "app-state"
  state.mkdir()
  pending = state / "pending.json"
  marker = state / "discovery.json"
  marker.write_text(json.dumps({
    "schema": 1,
    "newest": {"recency_at": "2026-07-28T01:00:00", "id": "previous"},
  }))
  monkeypatch.setattr(memory_runner, "_PENDING_CHAT_IDS", pending)
  monkeypatch.setattr(memory_runner, "_CHAT_DISCOVERY", marker)
  monkeypatch.setattr(
    memory_runner,
    "_api_result",
    lambda _path: memory_runner.ApiResult({
      "items": [
        {
          "id": "empty-newest", "recency_at": "2026-07-30T02:00:00",
          "message_count": 0,
        },
        {
          "id": "empty-newer", "recency_at": "2026-07-29T02:00:00",
          "message_count": 0,
        },
        {
          "id": "older", "recency_at": "2026-07-27T01:00:00",
          "message_count": 2,
        },
      ],
      "next_before": None,
    }, 200),
  )

  ids, complete, queue_ok = memory_runner._discover_chat_ids()

  assert ids == []
  assert complete is queue_ok is True
  assert memory_runner._load_pending_chat_ids() == []
  assert json.loads(marker.read_text())["newest"] == {
    "recency_at": "2026-07-30T02:00:00", "id": "empty-newest",
  }


def test_chat_intake_prunes_404s_but_retries_transient_failures(
  monkeypatch, tmp_path,
):
  pending = tmp_path / "pending-chat-ids.json"
  pending.write_text(json.dumps({
    "schema": 1,
    "chat_ids": ["gone", "transient", "empty", "good", "recent"],
  }))
  monkeypatch.setattr(memory_runner, "_PENDING_CHAT_IDS", pending)
  monkeypatch.setattr(
    memory_runner, "_discover_chat_ids", lambda: (["recent"], True, True),
  )

  def api(path):
    split = urllib.parse.urlsplit(path)
    chat_id = urllib.parse.unquote(split.path.rsplit("/", 1)[-1])
    assert urllib.parse.parse_qs(split.query)["include_deleted"] == ["true"]
    if chat_id == "gone":
      return memory_runner.ApiResult(None, 404, "http_error")
    if chat_id == "transient":
      return memory_runner.ApiResult(None, 503, "http_error")
    messages = [] if chat_id == "empty" else [
      {"role": "user", "text": f"content from {chat_id}"},
    ]
    return memory_runner.ApiResult({
      "id": chat_id,
      "title": chat_id,
      "updated_at": "2026-07-30T00:00:00",
      "deleted_at": (
        "2026-07-30T01:00:00" if chat_id == "recent" else None
      ),
      "messages": messages,
    }, 200)

  monkeypatch.setattr(memory_runner, "_api_result", api)

  intake = memory_runner._collect_chat_intake()

  assert [chat["id"] for chat in intake.chats] == ["good", "recent"]
  assert intake.chats[-1]["deleted_at"] == "2026-07-30T01:00:00"
  assert intake.tombstone_count == 1
  assert intake.tombstone_ids == ("gone",)
  assert intake.detail_failure_count == 1
  assert memory_runner._load_pending_chat_ids() == [
    "transient", "good", "recent",
  ]


def test_pending_chat_queue_preserves_more_than_the_old_fixed_window(
  monkeypatch, tmp_path,
):
  pending = tmp_path / "pending-chat-ids.json"
  monkeypatch.setattr(memory_runner, "_PENDING_CHAT_IDS", pending)
  ids = [f"chat-{index}" for index in range(700)]

  assert memory_runner._remember_pending_chat_ids(ids) is True
  assert memory_runner._load_pending_chat_ids() == ids


def test_chat_intake_processes_the_pending_queue_in_order(monkeypatch, tmp_path):
  pending = tmp_path / "pending-chat-ids.json"
  pending.write_text(json.dumps({
    "schema": 1,
    "chat_ids": ["waiting-one", "waiting-two", "newly-discovered"],
  }))
  monkeypatch.setattr(memory_runner, "_PENDING_CHAT_IDS", pending)
  monkeypatch.setattr(
    memory_runner,
    "_discover_chat_ids",
    lambda: (["newly-discovered"], True, True),
  )
  requested = []

  def api(path):
    chat_id = urllib.parse.unquote(
      urllib.parse.urlsplit(path).path.rsplit("/", 1)[-1],
    )
    requested.append(chat_id)
    return memory_runner.ApiResult({
      "id": chat_id,
      "title": chat_id,
      "updated_at": "2026-08-10T00:00:00Z",
      "messages": [{"role": "user", "text": chat_id}],
    }, 200)

  monkeypatch.setattr(memory_runner, "_api_result", api)

  intake = memory_runner._collect_chat_intake(limit=2)

  assert requested == ["waiting-one", "waiting-two"]
  assert [chat["id"] for chat in intake.chats] == requested


def test_deleted_chat_prompt_uses_non_linking_provenance(tmp_path):
  chat = {
    "id": "deleted-chat-id",
    "title": "A deleted conversation",
    "updated_at": "2026-07-30T00:00:00",
    "deleted_at": "2026-07-30T01:00:00",
    "messages": [{"role": "user", "text": "I prefer concise reports."}],
  }
  encoded, included = memory_runner._proposal_envelope(tmp_path, [chat], [])
  payload = json.loads(encoded)

  assert included == [chat]
  staged = payload["redacted_recent_chats"][0]
  assert staged["source_handle"] == "deleted:d01"
  assert "deleted-chat-id" not in encoded
  assert memory_runner._source_handles([chat]) == {}


def test_deleted_chat_handle_is_expanded_during_provider_validation(
  monkeypatch, tmp_path,
):
  chat = {
    "id": "deleted-chat-id",
    "title": "A deleted conversation",
    "updated_at": "2026-07-30T00:00:00",
    "deleted_at": "2026-07-30T01:00:00",
    "messages": [{"role": "user", "text": "I prefer concise reports."}],
  }
  proposal = {
    "updates": [{
      "path": "notes/concise.md",
      "content": (
        "---\ntype: note\ntitle: Concise reports are preferred\n"
        "source: [deleted:d01]\n---\nConcise reports are preferred.\n"
      ),
    }],
    "deletes": [],
    "followups": [],
    "read_audits": [],
    "self_review": _self_review(),
  }
  providers = memory_runner.ProviderPool([
    {"provider": "codex", "model": "gpt-test", "effort": None},
  ])
  monkeypatch.setattr(memory_runner, "_SOURCE_ARCHIVE_KEY", tmp_path / "key")
  monkeypatch.setattr(memory_runner, "_proposal_prompt", lambda *_args: "prompt")
  monkeypatch.setattr(memory_runner, "_known_chat_sources", lambda _path: set())
  monkeypatch.setattr(memory_runner, "_known_deleted_source_ids", lambda _path: set())
  monkeypatch.setattr(memory_runner, "_known_deleted_source", lambda _path: False)
  monkeypatch.setattr(
    memory_runner, "run_text", lambda *_args, **_kwargs: TextResult(json.dumps(proposal)),
  )

  outcome = memory_runner._proposal(57, tmp_path, [chat], [], providers)

  assert outcome.status == "ok"
  content = outcome.proposal["updates"][0]["content"]
  assert "source: [deleted-chat:" in content
  assert "deleted:d01" not in content
  assert chat["id"] not in content


def test_deleted_chat_source_is_accepted_only_when_available():
  proposal = {
    "updates": [{
      "path": "notes/concise.md",
      "content": (
        "---\n"
        "type: note\n"
        "title: Concise reports are preferred\n"
        "source: [deleted-chat]\n"
        "---\n"
        "Concise reports are preferred.\n"
      ),
    }],
    "deletes": [],
    "followups": [],
    "read_audits": [],
    "self_review": _self_review(),
  }

  normalized = memory_runner._normalize_proposal(
    proposal,
    allowed_chat_ids=set(),
    source_handles={},
    allow_deleted_source=True,
  )
  assert normalized["updates"] == proposal["updates"]

  with pytest.raises(memory_runner.ProposalValidationError) as raised:
    memory_runner._normalize_proposal(
      proposal,
      allowed_chat_ids=set(),
      source_handles={},
      allow_deleted_source=False,
    )
  assert raised.value.code == "unverified_chat_provenance"

  leaked_body = {
    **proposal,
    "updates": [{
      **proposal["updates"][0],
      "content": proposal["updates"][0]["content"].replace(
        "Concise reports are preferred.",
        "Concise reports are preferred (deleted-chat-id).",
      ),
    }],
  }
  with pytest.raises(memory_runner.ProposalValidationError) as raised:
    memory_runner._normalize_proposal(
      leaked_body,
      allowed_chat_ids=set(),
      source_handles={},
      allow_deleted_source=True,
      forbidden_chat_ids={"deleted-chat-id"},
    )
  assert raised.value.code == "deleted_chat_identifier"

  raw_id = {
    **proposal,
    "updates": [{
      **proposal["updates"][0],
      "content": proposal["updates"][0]["content"].replace(
        "deleted-chat", "chat:deleted-chat-id",
      ),
    }],
  }
  with pytest.raises(memory_runner.ProposalValidationError) as raised:
    memory_runner._normalize_proposal(
      raw_id,
      allowed_chat_ids=set(),
      source_handles={},
      allow_deleted_source=True,
    )
  assert raised.value.code == "unverified_chat_provenance"

  marker_outside_source = {
    **proposal,
    "updates": [{
      **proposal["updates"][0],
      "content": (
        "---\n"
        "type: note\n"
        "description: learned from deleted-chat\n"
        "source: []\n"
        "---\n"
        "Concise reports are preferred.\n"
      ),
    }],
  }
  with pytest.raises(memory_runner.ProposalValidationError) as raised:
    memory_runner._normalize_proposal(
      marker_outside_source,
      allowed_chat_ids=set(),
      source_handles={},
      allow_deleted_source=True,
    )
  assert raised.value.code == "unverified_chat_provenance"


def test_deleted_chat_backlinks_are_anonymized_and_deduplicated(
  tmp_path, monkeypatch,
):
  monkeypatch.setattr(
    memory_runner, "_SOURCE_ARCHIVE_KEY", tmp_path / "source-key.json",
  )
  notes = tmp_path / "notes"
  notes.mkdir()
  note = notes / "preference.md"
  note.write_text(
    "---\n"
    "type: note\n"
    "source: [chat:deleted-one, chat:active, chat:deleted-two]\n"
    "---\n"
    "A durable preference.\n",
    encoding="utf-8",
  )

  changed = memory_runner._anonymize_deleted_chat_sources(
    tmp_path, {"deleted-one", "deleted-two"},
  )

  assert changed == ["notes/preference.md"]
  text = note.read_text(encoding="utf-8")
  assert "chat:deleted-one" not in text
  assert "chat:deleted-two" not in text
  assert re.search(
    r"source: \[deleted-chat:[0-9a-f]{32}, chat:active, "
    r"deleted-chat:[0-9a-f]{32}\]",
    text,
  )
  assert memory_runner._known_deleted_source(tmp_path) is True


def test_source_archive_retains_reviewed_text_and_scrubs_deleted_chat_id(
  tmp_path, monkeypatch,
):
  monkeypatch.setattr(
    memory_runner, "_SOURCE_ARCHIVE_KEY", tmp_path / "source-key.json",
  )
  staging = tmp_path / "repository"
  (staging / "sources").mkdir(parents=True)
  active = {
    "id": "chat-source-one",
    "title": "A useful conversation",
    "updated_at": "2026-07-30T00:00:00",
    "deleted_at": None,
    "messages": [
      {"role": "user", "text": "I prefer concise reports."},
      {"role": "assistant", "text": "Understood."},
    ],
  }

  source_id, changed = memory_runner._archive_chat_source(
    staging, active, reviewed=True, capture_kind="analyst",
  )
  assert changed is True
  path = staging / "sources" / f"{source_id}.json"
  record = json.loads(path.read_text())
  assert record["chat_id"] == "chat-source-one"
  assert record["snapshots"][0]["reviewed"] is True
  assert record["snapshots"][0]["input"]["messages"] == active["messages"]

  deleted = {
    **active,
    "deleted_at": "2026-07-31T00:00:00",
  }
  next_source_id, changed = memory_runner._archive_chat_source(
    staging, deleted, reviewed=False, capture_kind="retention",
  )
  assert next_source_id == source_id
  assert changed is True
  record = json.loads(path.read_text())
  assert "chat_id" not in record
  assert "chat-source-one" not in path.read_text()
  assert record["deleted_at"] == "2026-07-31T00:00:00"
  assert len(record["snapshots"]) == 2


def test_purged_source_retirement_scrubs_chat_id_without_a_detail_record(
  tmp_path, monkeypatch,
):
  monkeypatch.setattr(
    memory_runner, "_SOURCE_ARCHIVE_KEY", tmp_path / "source-key.json",
  )
  staging = tmp_path / "repository"
  (staging / "sources").mkdir(parents=True)
  chat = {
    "id": "purged-source",
    "title": "A retained source",
    "updated_at": "2026-07-30T00:00:00",
    "deleted_at": None,
    "messages": [{"role": "user", "text": "A durable fact."}],
  }
  source_id, _ = memory_runner._archive_chat_source(
    staging, chat, reviewed=True, capture_kind="analyst",
  )

  next_source_id, changed = memory_runner._retire_unavailable_chat_source(
    staging, "purged-source",
  )

  assert next_source_id == source_id
  assert changed is True
  record = json.loads(
    (staging / "sources" / f"{source_id}.json").read_text(),
  )
  assert "chat_id" not in record
  assert record["source_unavailable_at"]
  assert "purged-source" not in json.dumps(record)


def test_archived_source_lifecycle_detects_deleted_and_purged_chats(
  tmp_path, monkeypatch,
):
  monkeypatch.setattr(
    memory_runner,
    "_archived_active_chat_ids",
    lambda _staging: {"active", "deleted", "purged", "already-checked"},
  )

  def detail(chat_id):
    if chat_id == "purged":
      return None, 404
    return ({
      "id": chat_id,
      "deleted_at": "2026-08-01T00:00:00" if chat_id == "deleted" else None,
    }, 200)

  monkeypatch.setattr(memory_runner, "_fetch_chat_detail", detail)

  deleted, unavailable = memory_runner._collect_archived_source_lifecycle(
    tmp_path, {"already-checked"},
  )

  assert deleted == [{
    "id": "deleted", "deleted_at": "2026-08-01T00:00:00",
  }]
  assert unavailable == {"purged"}


def test_source_catalog_marks_a_scrubbed_archive_deleted(tmp_path):
  (tmp_path / "sources").mkdir()
  source_id = "c" * 32
  (tmp_path / "sources" / f"{source_id}.json").write_text(json.dumps({
    "schema": 1,
    "source_id": source_id,
    "title": "Purged source",
    "deleted_at": None,
    "source_unavailable_at": "2026-08-01T00:00:00+00:00",
    "snapshots": [],
  }))

  by_id, by_chat_id = memory_graph._source_catalog(tmp_path)

  assert by_id[source_id]["kind"] == "deleted"
  assert by_chat_id == {}


def test_deleted_source_handle_expands_to_opaque_retained_source():
  source_id = "a" * 32
  proposal = {
    "updates": [{
      "path": "notes/concise.md",
      "content": (
        "---\n"
        "type: note\n"
        "title: Concise reports are preferred\n"
        "source: [deleted:d01]\n"
        "---\n"
        "Concise reports are preferred.\n"
      ),
    }],
    "deletes": [],
    "followups": [],
    "read_audits": [],
    "self_review": _self_review(),
  }

  normalized = memory_runner._normalize_proposal(
    proposal,
    allowed_chat_ids=set(),
    deleted_source_handles={"d01": source_id},
    allowed_deleted_source_ids={source_id},
    allow_deleted_source=True,
  )
  assert f"source: [deleted-chat:{source_id}]" in (
    normalized["updates"][0]["content"]
  )


def test_graph_catalog_links_note_to_source_archive(tmp_path):
  (tmp_path / "mocs").mkdir()
  (tmp_path / "notes").mkdir()
  (tmp_path / "sources").mkdir()
  (tmp_path / "index.md").write_text(
    "---\ntype: moc\ntitle: Memory\n---\n[[topic]]\n",
  )
  (tmp_path / "mocs" / "topic.md").write_text(
    "---\ntype: moc\ntitle: Topic\n---\n[[fact]]\n",
  )
  (tmp_path / "notes" / "fact.md").write_text(
    "---\ntype: note\ntitle: A fact\nsource: [chat:active-chat]\n"
    "mocs: [topic]\n---\nA fact.\n",
  )
  source_id = "b" * 32
  (tmp_path / "sources" / f"{source_id}.json").write_text(json.dumps({
    "schema": 1,
    "source_id": source_id,
    "chat_id": "active-chat",
    "title": "Source conversation",
    "deleted_at": None,
    "snapshots": [{"hash": "x"}],
  }))

  graph = memory_graph.build(tmp_path)
  fact = next(node for node in graph["nodes"] if node["id"] == "fact")
  assert fact["source_refs"] == [{
    "source_id": source_id,
    "file": f"sources/{source_id}.json",
    "kind": "active",
    "title": "Source conversation",
    "snapshot_count": 1,
  }]


def test_maintenance_routes_app_owned_warnings_without_repeated_writer_work(
  tmp_path,
):
  (tmp_path / "notes").mkdir()
  (tmp_path / "mocs").mkdir()
  (tmp_path / "index.md").write_text(
    "---\ntype: moc\ntitle: Memory\n---\n"
    "[[maintaining-memory]]\n[[writer-owned]]\n",
    encoding="utf-8",
  )
  long_body = "\n".join(f"line {index}" for index in range(31))
  (tmp_path / "mocs" / "maintaining-memory.md").write_text(
    "---\ntype: note\ntitle: Owned\nmanaged_by: memory\n---\n" + long_body,
    encoding="utf-8",
  )
  (tmp_path / "notes" / "writer-owned.md").write_text(
    "---\ntype: note\ntitle: Writer owned\n---\n" + long_body,
    encoding="utf-8",
  )
  graph = memory_graph.build(tmp_path)
  graph["problems"].append(dict(graph["problems"][0]))
  (tmp_path / "graph.json").write_text(json.dumps(graph), encoding="utf-8")

  diagnostics = memory_runner._maintenance_diagnostics(tmp_path)
  flags = memory_runner._maintenance_flags(tmp_path)

  owned = [
    item for item in diagnostics
    if item["path"] == "mocs/maintaining-memory.md"
  ]
  assert owned == [{
    "code": "graph.oversized_note",
    "kind": "oversized_note",
    "severity": "warning",
    "node": "maintaining-memory",
    "path": "mocs/maintaining-memory.md",
    "owner": "memory",
    "actionable_by_writer": False,
    "lines": 31,
  }]
  assert [item["path"] for item in flags] == ["notes/writer-owned.md"]
  assert flags[0]["code"] == "graph.oversized_note"


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


def test_audit_prompt_view_deduplicates_frontier_catalog_metadata(
  monkeypatch, tmp_path,
):
  audit = {
    "read_id": "read-1",
    "question": "What would help?",
    "live": {
      "selected": ["notes/useful.md"],
      "frontier_at_stop": [{
        "id": "useful", "path": "notes/useful.md", "title": "Useful",
        "description": "x" * 2_000,
      }],
    },
    "deep": {
      "selected": ["notes/useful.md"],
      "selected_nodes": [{
        "path": "notes/useful.md", "title": "Useful",
        "content": "Outcome evidence stays complete.",
      }],
      "frontier_at_stop": [{
        "id": "other", "path": "notes/other.md", "title": "Other",
        "description": "y" * 2_000,
      }],
      "decisions": [{
        "round": 1, "selected": ["useful"], "reason": "Relevant.",
        "attempts": [{"usage_receipt": {"input_chars": 90_000}}],
      }],
    },
    "hindsight_chat": {"messages": ["It helped."]},
  }
  monkeypatch.setattr(memory_runner, "_MAX_PROMPT_DATA_CHARS", 1_200)
  monkeypatch.setattr(memory_runner, "_maintenance_flags", lambda _path: [])
  monkeypatch.setattr(
    memory_runner, "_graph_prompt_context",
    lambda _path: ([{
      "id": "index", "path": "index.md", "title": "Memory",
      "description": "Root", "content": "Root", "content_complete": True,
    }], []),
  )

  encoded, _ = memory_runner._proposal_envelope(tmp_path, [], [audit])
  supplied = json.loads(encoded)["read_audits"][0]

  assert supplied["live"]["frontier_at_stop"] == [{
    "id": "useful", "path": "notes/useful.md", "title": "Useful",
  }]
  assert supplied["deep"]["frontier_at_stop"] == [{
    "id": "other", "path": "notes/other.md", "title": "Other",
  }]
  assert supplied["deep"]["selected_nodes"][0]["content"] == (
    "Outcome evidence stays complete."
  )
  assert supplied["deep"]["decisions"] == [{
    "round": 1, "selected": ["useful"], "reason": "Relevant.",
  }]
  assert audit["live"]["frontier_at_stop"][0]["description"] == "x" * 2_000


def test_audit_prompt_view_preserves_grouped_frontier_route_references():
  audit = {
    "live": {"frontier_at_stop": [{
      "depth": 1, "from": "index", "description": "drop",
      "nodes": [{
        "id": "useful", "path": "notes/useful.md",
        "title": "Useful", "description": "drop",
      }],
    }]},
    "deep": {"frontier_at_stop": []},
  }

  supplied = memory_runner._audit_prompt_view(audit)

  assert supplied["live"]["frontier_at_stop"] == [{
    "depth": 1, "from": "index",
    "nodes": [{"id": "useful"}],
  }]
  assert audit["live"]["frontier_at_stop"][0]["nodes"][0]["title"] == "Useful"


def test_audit_envelope_compacts_redundant_ordinary_catalog_fields(
  monkeypatch, tmp_path,
):
  monkeypatch.setattr(memory_runner, "_maintenance_flags", lambda _path: [])
  monkeypatch.setattr(memory_runner, "_rank_note_contents", lambda graph, notes, chats: notes)
  monkeypatch.setattr(
    memory_runner, "_graph_prompt_context",
    lambda _path: ([
      {
        "id": "index", "path": "index.md", "title": "Memory",
        "description": "Root", "content": "Root", "content_complete": True,
      },
      {
        "id": "useful", "path": "notes/useful.md", "title": "Useful",
        "description": "Useful fact", "content_complete": True,
      },
    ], []),
  )

  encoded, _ = memory_runner._proposal_envelope(
    tmp_path, [], [{"read_id": "one", "live": {}, "deep": {}}],
  )
  supplied = json.loads(encoded)["existing_graph"]

  assert supplied[0]["title"] == "Memory"
  assert "title" not in supplied[1]
  assert "description" not in supplied[1]
  assert "path" not in supplied[1]
  assert "content_complete" not in supplied[1]


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


def test_updated_note_text_preserves_updates_from_every_batch():
  proposals = [
    {
      "updates": [
        {"path": "notes/first.md", "content": "first source"},
        {"path": "mocs/topic.md", "content": "not a note"},
      ],
    },
    {
      "updates": [
        {"path": "notes/second.md", "content": "second source"},
      ],
    },
  ]

  assert memory_runner._updated_note_text(proposals) == [
    "first source", "second source",
  ]


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
    "next_experiment": " Try   a clearer route; expect fewer misses. ",
  }
  normalized = memory_runner._normalize_proposal(
    proposal, allowed_chat_ids=set(), source_handles={},
  )
  assert normalized["self_review"] == {
    "hardest_decision": "Pick the durable route.",
    "possibly_missed": "none",
    "prompt_change": "none",
    "next_experiment": "Try a clearer route; expect fewer misses.",
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
  def text(provider, _prompt, **_kwargs):
    calls.append(provider)
    if provider == "claude":
      return TextResult(
        None,
        memory_runner.ProviderFailure("usage_limit", True, "provider"),
      )
    return TextResult(json.dumps(proposal))

  monkeypatch.setattr(memory_runner, "run_text", text)

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
  def text(provider, _prompt, **_kwargs):
    calls.append(provider)
    if provider == "claude":
      return TextResult(None, memory_runner.ProviderFailure("timeout"))
    return TextResult(json.dumps(proposal))

  monkeypatch.setattr(memory_runner, "run_text", text)

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
  def text(provider, _prompt, **_kwargs):
    calls.append(provider)
    if provider == "claude":
      return TextResult(
        None,
        memory_runner.ProviderFailure("usage_limit", True, "provider"),
      )
    return TextResult(json.dumps(proposal))

  monkeypatch.setattr(memory_runner, "run_text", text)

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


def test_oversized_recall_audit_does_not_freeze_chat_consolidation(
  monkeypatch, tmp_path,
):
  graph = {"nodes": [], "edges": [], "problems": []}
  chats = [{"id": "chat-1"}]
  audits = [{"read_id": "oversized-audit"}]
  proposal = {
    "updates": [], "deletes": [], "summary": "Processed the chat.",
    "followups": [], "read_audits": [],
    "self_review": _self_review(),
  }
  outcome = memory_runner.ProposalOutcome(
    "ok", proposal, "codex", "gpt-test", [],
  )
  monkeypatch.setattr(
    memory_runner, "_audit_prompt_batch", lambda *_args: ([], 1),
  )
  monkeypatch.setattr(
    memory_runner, "_proposal_envelope", lambda *_args: ("{}", []),
  )
  monkeypatch.setattr(
    memory_runner, "_proposal_batch", lambda _staging, remaining, _audits: remaining,
  )
  monkeypatch.setattr(memory_runner, "_proposal", lambda *_args: outcome)
  monkeypatch.setattr(
    memory_runner, "_apply_validated_proposal",
    lambda _staging, value, **_kwargs: (value, [], [], graph),
  )

  result = memory_runner._consolidate_batches(
    57, tmp_path, graph, chats, audits, memory_runner.ProviderPool([]),
  )

  assert result.accepted_chats == chats
  assert result.accepted_audits == []
  assert result.remaining_chats == []
  assert result.deferred_reason == "read_audit_over_budget"
  assert result.deferred_detail == (
    "oldest Memory recall audit exceeds the analyst prompt budget"
  )
  assert result.rejected_audit_count == 1


def test_oversized_oldest_audit_does_not_starve_later_hindsight(
  monkeypatch, tmp_path,
):
  graph = {"nodes": [], "edges": [], "problems": []}
  oversized = {"read_id": "oversized-audit"}
  fitting = {"read_id": "fitting-audit"}
  proposal = {
    "updates": [], "deletes": [], "summary": "Reviewed later hindsight.",
    "followups": [], "read_audits": [{"read_id": "fitting-audit"}],
    "self_review": _self_review(),
  }
  outcome = memory_runner.ProposalOutcome(
    "ok", proposal, "codex", "gpt-test", [],
  )

  def audit_batch(_staging, remaining):
    return ([], len(remaining)) if remaining[0] is oversized else ([remaining[0]], 0)

  monkeypatch.setattr(memory_runner, "_audit_prompt_batch", audit_batch)
  monkeypatch.setattr(
    memory_runner, "_proposal_envelope", lambda *_args: ("{}", []),
  )
  monkeypatch.setattr(memory_runner, "_proposal", lambda *_args: outcome)
  monkeypatch.setattr(
    memory_runner, "_apply_validated_proposal",
    lambda _staging, value, **_kwargs: (value, [], [], graph),
  )

  result = memory_runner._consolidate_batches(
    57, tmp_path, graph, [], [oversized, fitting],
    memory_runner.ProviderPool([]),
  )

  assert result.accepted_audits == [fitting]
  assert result.rejected_audit_count == 1
  assert result.deferred_reason == "read_audit_over_budget"


def test_unfit_routing_context_remains_a_hard_failure(
  monkeypatch, tmp_path,
):
  graph = {"nodes": [], "edges": [], "problems": []}
  audits = [{"read_id": "audit"}]
  monkeypatch.setattr(
    memory_runner, "_audit_prompt_batch", lambda *_args: ([], 1),
  )

  def reject_routes(*_args):
    raise memory_runner.ProposalValidationError(
      "routing_context_over_budget", "required routes do not fit",
    )

  monkeypatch.setattr(memory_runner, "_proposal_envelope", reject_routes)

  with pytest.raises(memory_runner.ProposalValidationError) as raised:
    memory_runner._consolidate_batches(
      57, tmp_path, graph, [], audits, memory_runner.ProviderPool([]),
    )

  assert raised.value.code == "routing_context_over_budget"


@pytest.mark.parametrize(
  ("message", "code", "scope"),
  [
    ("Monthly usage limit reached", "usage_limit", "provider"),
    ("Authentication failed: please login", "authentication", "provider"),
    ("Unknown model claude-future", "model_unavailable", "choice"),
  ],
)
def test_terminal_provider_errors_have_typed_scope(message, code, scope):
  failure = classify_process_failure(1, stderr=message)

  assert failure.code == code
  assert failure.terminal is True
  assert failure.scope == scope


def test_rate_limit_is_not_cached_as_a_terminal_failure():
  failure = classify_process_failure(
    1, stderr="Temporary rate limit; retry later",
  )

  assert failure.code == "process_exit_1"
  assert failure.terminal is False


def test_provider_summary_keeps_failures_skips_and_successes_from_all_batches():
  outcomes = [
    memory_runner.ProposalOutcome(
      "ok", {}, "codex", "gpt-test", [
        {
          "provider": "claude", "model": "opus", "supported": True,
          "failure_code": "usage_limit", "disabled_for_run": True,
        },
        {
          "provider": "codex", "model": "gpt-test", "supported": True,
          "outcome": "accepted",
        },
      ],
    ),
    memory_runner.ProposalOutcome(
      "ok", {}, "codex", "gpt-test", [
        {
          "provider": "claude", "model": "opus", "supported": True,
          "skipped_reason": "usage_limit",
        },
        {
          "provider": "codex", "model": "gpt-test", "supported": True,
          "outcome": "accepted",
        },
      ],
    ),
  ]

  summary = {
    (item["provider"], item["model"]): item
    for item in memory_runner._provider_summary(outcomes)
  }

  assert summary[("claude", "opus")]["failures"] == {"usage_limit": 1}
  assert summary[("claude", "opus")]["skips"] == {"usage_limit": 1}
  assert summary[("claude", "opus")]["invoked"] == 1
  assert summary[("codex", "gpt-test")]["accepted"] == 2
  assert summary[("codex", "gpt-test")]["invoked"] == 2


def test_model_work_receipt_aggregates_batches_without_inventing_missing_cost():
  outcomes = [
    memory_runner.ProposalOutcome(
      "ok", {}, "claude", "opus", [{
        "provider": "claude", "model": "opus", "outcome": "accepted",
        "usage_receipt": {
          "input_chars": 1000, "output_chars": 200,
          "usage": {"input_tokens": 300, "output_tokens": 40},
          "cost_usd": 0.8,
        },
      }],
    ),
    memory_runner.ProposalOutcome(
      "ok", {}, "codex", "gpt", [{
        "provider": "codex", "model": "gpt", "outcome": "accepted",
        "usage_receipt": {
          "input_chars": 900, "output_chars": 150,
          "usage": {"input_tokens": 250, "output_tokens": 30},
          "cost_usd": None,
        },
      }],
    ),
  ]

  receipt = memory_runner._model_work_receipt(outcomes)

  assert receipt["attempt_count"] == 2
  assert receipt["usage_reported_attempts"] == 2
  assert receipt["cost_reported_attempts"] == 1
  assert receipt["reported_cost_usd"] == 0.8
  assert receipt["input_chars"] == 1900
  assert receipt["output_chars"] == 350
  assert receipt["token_usage"] == {
    "input_tokens": 550, "output_tokens": 70,
  }
  assert [item["batch"] for item in receipt["attempts"]] == [1, 2]


def test_model_work_receipt_keeps_fully_unreported_cost_unknown():
  receipt = memory_runner._aggregate_model_work([{
    "provider": "codex",
    "receipt": {
      "input_chars": 100,
      "output_chars": 20,
      "usage": {"input_tokens": 30},
      "cost_usd": None,
    },
  }])

  assert receipt["cost_reported_attempts"] == 0
  assert receipt["reported_cost_usd"] is None


def test_recall_audit_model_work_uses_deep_replay_receipts():
  receipt = memory_runner._recall_audit_model_work([{
    "read_id": "read-1",
    "deep": {"decisions": [{"attempts": [{
      "provider": "claude", "outcome": "ok",
      "usage_receipt": {
        "input_chars": 400, "output_chars": 80,
        "usage": {"input_tokens": 120, "output_tokens": 15},
        "cost_usd": 0.25,
      },
    }, {"provider": "lexical", "outcome": "fallback"}]}]},
  }, {"read_id": "read-2", "deep": {"decisions": []}}])

  assert receipt["attempt_count"] == 1
  assert receipt["reported_cost_usd"] == 0.25
  assert receipt["token_usage"] == {
    "input_tokens": 120, "output_tokens": 15,
  }
  assert receipt["attempts"][0]["read_id"] == "read-1"


def test_recall_model_work_uses_original_live_recall_receipts():
  receipt = memory_runner._recall_model_work([{
    "read_id": "read-1",
    "traversal": {"decisions": [{"attempts": [{
      "provider": "claude", "outcome": "ok",
      "usage_receipt": {
        "input_chars": 300, "output_chars": 60,
        "usage": {"input_tokens": 90, "output_tokens": 12},
        "cost_usd": 0.2,
      },
    }]}]},
  }, {"read_id": "read-2", "traversal": {"decisions": []}}])

  assert receipt["attempt_count"] == 1
  assert receipt["reported_cost_usd"] == 0.2
  assert receipt["token_usage"] == {
    "input_tokens": 90, "output_tokens": 12,
  }
  assert receipt["attempts"][0]["read_id"] == "read-1"


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
      baseline=baseline,
    )

  assert raised.value.code == "topology_regression"
  assert topic.read_text() == original
  assert builds == []


def test_structurally_invalid_batch_restores_files_and_derived_graph(
  monkeypatch, tmp_path,
):
  mocs = tmp_path / "mocs"
  mocs.mkdir()
  topic = mocs / "topic.md"
  original = "# Topic\n"
  topic.write_text(original)
  baseline = {
    "nodes": [{"id": "index"}, {"id": "topic"}],
    "edges": [{"source": "index", "target": "topic"}],
    "problems": [],
  }
  invalid = {
    "nodes": baseline["nodes"],
    "edges": baseline["edges"],
    "problems": [{
      "kind": "dangling_link", "source": "topic", "target": "missing",
      "severity": "error",
    }],
  }
  builds = [invalid, baseline]
  monkeypatch.setattr(
    memory_runner,
    "build_graph",
    lambda *_args, **_kwargs: builds.pop(0),
  )
  proposal = {
    "updates": [{
      "path": "mocs/topic.md", "content": "# Topic\n\n- [[missing]]\n",
    }],
    "deletes": [],
    "followups": [],
    "self_review": _self_review(),
  }

  with pytest.raises(memory_runner.ProposalValidationError) as raised:
    memory_runner._apply_validated_proposal(
      tmp_path,
      proposal,
      baseline=baseline,
    )

  assert raised.value.code == "invalid_graph"
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
  monkeypatch.setattr(
    memory_runner, "_SOURCE_ARCHIVE_KEY", tmp_path / "source-key.json",
  )
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
  monkeypatch.setattr(
    memory_runner, "_collect_chat_intake", lambda: memory_runner.ChatIntake([]),
  )
  monkeypatch.setattr(memory_runner, "_pending_read_traces", lambda: traces)

  def audit(_app_id, _commit, selected, _staging=None, _hindsight=None):
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
  monkeypatch.setattr(
    memory_runner,
    "_acknowledge_pending_chats",
    lambda _items: memory_runner.QueueAcknowledgement(
      write_ok=True, before_count=0, removed_count=0, remaining_count=0,
    ),
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
  monkeypatch.setattr(memory_runner, "_live_policy", lambda _app_id: 4)
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

  def acknowledge(selected):
    acknowledged.extend(chat["id"] for chat in selected)
    return memory_runner.QueueAcknowledgement(
      write_ok=True,
      before_count=len(selected),
      removed_count=len(selected),
      remaining_count=0,
    )

  monkeypatch.setattr(memory_runner, "_app_id", lambda: 57)
  monkeypatch.setattr(memory_runner, "APP_TOKEN", "scoped-token")
  monkeypatch.setattr(
    memory_runner, "_SOURCE_ARCHIVE_KEY", tmp_path / "source-key.json",
  )
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
  monkeypatch.setattr(
    memory_runner,
    "_collect_chat_intake",
    lambda: memory_runner.ChatIntake(chats, pending_count=len(chats)),
  )
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
    acknowledge,
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
  monkeypatch.setattr(memory_runner, "_live_policy", lambda _app_id: 4)
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


@pytest.mark.parametrize("rejection_code", ["topology_regression", "invalid_graph"])
def test_run_publishes_accepted_batches_and_defers_structural_rejection(
  monkeypatch, tmp_path, rejection_code,
):
  chats = [{"id": f"chat-{index}"} for index in range(3)]
  acknowledged = []
  statuses = []
  graph = {"nodes": [], "edges": [], "problems": []}
  apply_count = 0

  def acknowledge(selected):
    acknowledged.extend(chat["id"] for chat in selected)
    return memory_runner.QueueAcknowledgement(
      write_ok=True,
      before_count=len(selected),
      removed_count=len(selected),
      remaining_count=0,
    )

  monkeypatch.setattr(memory_runner, "_app_id", lambda: 57)
  monkeypatch.setattr(memory_runner, "APP_TOKEN", "scoped-token")
  monkeypatch.setattr(
    memory_runner, "_SOURCE_ARCHIVE_KEY", tmp_path / "source-key.json",
  )
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
  monkeypatch.setattr(
    memory_runner,
    "_collect_chat_intake",
    lambda: memory_runner.ChatIntake(chats, pending_count=len(chats)),
  )
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
        rejection_code, "candidate graph is not publishable",
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
    acknowledge,
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
  monkeypatch.setattr(memory_runner, "_live_policy", lambda _app_id: 4)
  monkeypatch.setattr(memory_runner, "_night_policy", lambda _app_id: (6, 6))

  assert asyncio.run(memory_runner.run()) == 0
  assert acknowledged == ["chat-0", "chat-1"]
  assert statuses[-1]["status"] == "published"
  assert statuses[-1]["source_chat_count"] == 2
  assert statuses[-1]["deferred_chat_count"] == 1
  assert statuses[-1]["deferred_reason"] == rejection_code
  assert statuses[-1]["deferred_attempted_agents"][-1][
    "rejection_code"
  ] == rejection_code


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
  assert "make future recall more useful" in prompt
  assert "`next_experiment`" in prompt
  assert "use the later conversation as the primary" in prompt
  assert "Deep replay remains diagnostic evidence" in prompt
  assert '`usefulness` as `helpful`, `mixed`, `unused`, `harmful`, or' in prompt
  assert '"hindsight_reason":"short outcome-based reason"' in prompt
  assert '"read_id": "read-1"' in prompt


def test_recall_hindsight_reuses_intake_and_fetches_each_missing_chat_once(monkeypatch):
  fetched = []
  known = {"known": {"id": "known", "messages": []}}

  def fetch(chat_id):
    fetched.append(chat_id)
    return ({"id": chat_id, "messages": [{"role": "user", "text": "later"}]}, 200)

  monkeypatch.setattr(memory_runner, "_fetch_chat_detail", fetch)
  result = memory_runner._recall_hindsight_chats([
    {"chat_id": "known"}, {"chat_id": "missing"},
    {"chat_id": "missing"}, {"chat_id": "not valid!"},
  ], known)

  assert set(result) == {"known", "missing"}
  assert fetched == ["missing"]


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


def test_read_cursor_does_not_reopen_older_daily_logs(monkeypatch, tmp_path):
  state = tmp_path / "app-state"
  old = state / "read-log" / "2026-07-27.jsonl"
  current = state / "read-log" / "2026-07-28.jsonl"
  current.parent.mkdir(parents=True)
  old.write_text("this file must not be reopened\n")
  current.write_text(json.dumps({
    "schema": 3,
    "read_id": "new",
    "at": "2026-07-28T01:00:00+00:00",
    "question": "new question",
  }) + "\n")
  stats_path = state / "recall-stats.json"
  stats_path.write_text(json.dumps({
    "last_audited_at": "2026-07-28T00:30:00+00:00",
  }))
  monkeypatch.setattr(memory_runner, "STATE", state)
  monkeypatch.setattr(memory_runner, "_RECALL_STATS", stats_path)
  original = Path.read_text

  def guarded_read(path, *args, **kwargs):
    if path == old:
      raise AssertionError("older immutable read log was reopened")
    return original(path, *args, **kwargs)

  monkeypatch.setattr(Path, "read_text", guarded_read)

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
    "usefulness": "mixed",
    "hindsight_reason": "The recalled context helped, but the agent rediscovered the missing distinction.",
  }]}
  graph = {"nodes": [{}, {}, {}], "edges": [{}, {}]}

  memory_runner._record_recall_audits(
    "run-1",
    audits,
    proposal,
    graph,
    live_policy=4,
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
  assert stats["live_policy"] == {"selection": "one_pass", "depth": 4}
  assert stats["night_policy"] == {"breadth": 6, "depth": 6}
  assert stats["usefulness_counts"] == {
    "helpful": 0, "mixed": 1, "unused": 0, "harmful": 0, "unknown": 0,
  }
  assert stats["hindsight_assessed"] == 1
  assert stats["recent"][-1]["usefulness"] == "mixed"


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
    live_policy=4, night_policy=(6, 6),
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
      "content_complete": True,
    },
    {
      "id": "topic",
      "path": "mocs/topic.md",
      "title": "Topic",
      "description": "Route",
      "content": "r" * 200,
      "content_complete": True,
    },
    *[
      {
        "id": f"note-{index}",
        "path": f"notes/note-{index}.md",
        "title": f"Note {index}",
        "description": "Existing fact",
        "content": "n" * 250,
        "content_complete": True,
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


def test_note_body_ranking_uses_bounded_chat_and_compact_metadata():
  graph = [
    {
      "path": "notes/coffee.md", "title": "Coffee preferences",
      "description": "Espresso workflow and grinder constraints",
    },
    {
      "path": "notes/deploy.md", "title": "Deploy notes",
      "description": "Server rollout history",
    },
  ]
  bodies = [
    {"path": "notes/deploy.md", "content": "deployment body"},
    {"path": "notes/coffee.md", "content": "coffee body"},
  ]
  chats = [{
    "id": "chat-one",
    "title": "Dialing in espresso",
    "messages": [{"role": "user", "text": "Remember my grinder constraints"}],
    # Raw fields outside the bounded chat must not skew relevance.
    "tool_dump": "deploy server rollout " * 100,
  }]

  ranked = memory_runner._rank_note_contents(graph, bodies, chats)

  assert [item["path"] for item in ranked] == [
    "notes/coffee.md", "notes/deploy.md",
  ]


def test_note_body_ranking_has_deterministic_path_tiebreaker():
  bodies = [
    {"path": "notes/zeta.md", "content": "z"},
    {"path": "notes/alpha.md", "content": "a"},
  ]
  assert [
    item["path"]
    for item in memory_runner._rank_note_contents([], bodies, [])
  ] == ["notes/alpha.md", "notes/zeta.md"]


def test_ranked_note_bodies_yield_to_a_chat_at_the_prompt_boundary(
  monkeypatch, tmp_path,
):
  monkeypatch.setattr(memory_runner, "_MAX_PROMPT_DATA_CHARS", 1500)
  monkeypatch.setattr(memory_runner, "_maintenance_flags", lambda _staging: [])
  monkeypatch.setattr(memory_runner, "_graph_catalog", lambda _staging: [
    {
      "id": "index", "path": "index.md", "title": "Memory",
      "description": "Root", "content": "r" * 80,
      "content_complete": True,
    },
    *[
      {
        "id": f"note-{index}", "path": f"notes/note-{index}.md",
        "title": "Espresso grinder" if index == 0 else f"Other {index}",
        "description": "Coffee constraint" if index == 0 else "Unrelated",
        "content": "n" * 130, "content_complete": True,
      }
      for index in range(4)
    ],
  ])
  chats = [{
    "id": "chat-one",
    "title": "Espresso",
    "messages": [{
      "role": "user", "text": "Remember my coffee grinder constraint",
    }],
  }]

  encoded, included = memory_runner._proposal_envelope(tmp_path, chats, [])
  payload = json.loads(encoded)

  assert included == chats
  assert 0 < len(payload["existing_note_contents"]) < 4
  assert payload["existing_note_contents"][0]["path"] == "notes/note-0.md"


def test_recall_stats_migration_compacts_without_new_audits(
  monkeypatch, tmp_path,
):
  target = tmp_path / "recall-stats.json"
  target.write_text(json.dumps({
    "schema": 3,
    "reads_audited": 1,
    "recent": [{
      "schema": 3,
      "read_id": "read-one",
      "at": "2026-08-01T00:00:00+00:00",
      "outcome": "hit",
      "live_frontier_at_stop": [{"large": "payload"}],
    }],
  }))
  monkeypatch.setattr(memory_runner, "_RECALL_STATS", target)

  assert memory_runner._migrate_recall_stats() is True
  migrated = json.loads(target.read_text())
  assert migrated["reads_audited"] == 1
  assert migrated["recent"][0]["read_id"] == "read-one"
  assert "live_frontier_at_stop" not in migrated["recent"][0]
  assert memory_runner._migrate_recall_stats() is False


def test_graph_catalog_never_silently_truncates_large_graphs(tmp_path):
  nodes = [
    {
      "id": f"note-{index}",
      "path": f"notes/note-{index}.md",
      "title": f"Note {index}",
      "description": "A compact fact.",
    }
    for index in range(620)
  ]
  (tmp_path / "graph.json").write_text(json.dumps({"nodes": nodes}))

  catalog = memory_runner._graph_catalog(tmp_path)
  scale = memory_runner._graph_context_scale(tmp_path)

  assert len(catalog) == 620
  assert scale["catalog_nodes"] == 620
  assert scale["required_context_chars"] > 0


def test_memory_prompt_keeps_lookup_invocation_isolated():
  prompt = MEMORY_CORE_PROMPT

  assert "python3 <this installed system app's source_dir>/memory_search.py" in prompt
  assert "/data/apps/memory/memory_search" not in prompt
  assert "own exact exec invocation" in prompt
  assert "pipes, redirects, or other shell operations" in prompt
  assert "isolation describes the command shape, not the schedule" in prompt
  assert "Dispatch the Memory invocation in parallel" in prompt


def test_memory_prompt_balances_recall_with_direct_evidence():
  prompt = MEMORY_CORE_PROMPT

  assert (
    "Memory is an additive context lane, never a gate in front of the work."
  ) in prompt
  assert "could materially improve the work" in prompt
  assert (
    "begin every independent investigation as if Memory were unavailable"
  ) in prompt
  assert (
    "A technically detailed or fully specified request can still warrant recall "
    "when one of those cues is present."
  ) in prompt
  assert "the task is self-contained and its desired outcome is fully specified" in prompt
  assert "Complexity alone is not a cue." in prompt
  assert "owning sources establish what is true now and what happened" in prompt
  assert "A separate Memory lookup may run in parallel" in prompt
  assert "mention the concrete mismatch in the visible conversation" in prompt
  assert "Never infer an exact requirement from a broader memory" in prompt


def test_memory_prompt_keeps_retrieval_queries_durable_and_safe():
  prompt = MEMORY_CORE_PROMPT

  assert "Never request credentials or secrets" in prompt
  assert (
    "current account or configuration state, exact records or transactions, "
    "or implementation history"
  ) in prompt


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
    "content_complete": True,
  }])

  with pytest.raises(memory_runner.ProposalValidationError) as raised:
    memory_runner._proposal_envelope(tmp_path, [], [])

  assert raised.value.code == "routing_context_over_budget"


def test_prompt_rejects_a_routing_document_that_cannot_be_supplied_complete(
  monkeypatch, tmp_path,
):
  monkeypatch.setattr(memory_runner, "_maintenance_flags", lambda _staging: [])
  monkeypatch.setattr(memory_runner, "_graph_catalog", lambda _staging: [{
    "id": "topic",
    "path": "mocs/topic.md",
    "title": "Topic",
    "description": "Route",
    "content": "",
    "content_complete": False,
  }])

  with pytest.raises(memory_runner.ProposalValidationError) as raised:
    memory_runner._proposal_envelope(tmp_path, [], [])

  assert raised.value.code == "routing_document_too_large"


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
