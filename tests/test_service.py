"""Memory's agent tools as served through the platform's app service."""

from __future__ import annotations

import json
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service  # noqa: E402

MANIFEST = json.loads(
  (Path(__file__).resolve().parents[1] / "mobius.json").read_text("utf-8"),
)


def _request(path, arguments, call=None):
  return {
    "schema": 1, "method": "POST", "path": path, "query": {}, "headers": {},
    "body": {
      "arguments": arguments,
      "call": {"chat_id": "chat-1", "run_id": "run-1"} if call is None else call,
    },
    "public": False, "actor": {"scope": "platform"},
  }


@pytest.fixture
def recorded(monkeypatch):
  calls = []

  def fake(name):
    def run(args):
      calls.append((name, args))
      print("text for the agent")
      print('MOBIUS_APP_ACTIVITY_V1:{"activity_id":"memory-search"}')
      return 0
    return run

  import memory_read
  import memory_search
  import remember
  monkeypatch.setattr(memory_search, "run", fake("search"))
  monkeypatch.setattr(memory_read, "run", fake("read"))
  monkeypatch.setattr(remember, "main", fake("remember"))
  return calls


def test_every_declared_tool_has_a_handler_and_an_activity_card():
  declared = {tool["name"] for tool in MANIFEST["tools"]}
  assert {f"/tools/{name}" for name in declared} == set(service.TOOLS)
  carded = {entry["tool"] for entry in MANIFEST["agent_activities"].values()}
  assert carded == declared


def test_search_is_scoped_to_the_calling_chat_and_returns_the_receipt(recorded):
  response = service.dispatch(_request("/tools/search", {"query": "prefs"}))

  assert response["status"] == 200
  assert response["body"].endswith(
    'MOBIUS_APP_ACTIVITY_V1:{"activity_id":"memory-search"}\n'
  )
  assert recorded == [("search", ["prefs", "chat-1"])]


def test_read_accepts_a_list_selection_and_defaults_the_cursor(recorded):
  service.dispatch(_request(
    "/tools/read", {"lookup_id": "abc", "selection": ["note-a", "note-b"]},
  ))
  assert recorded == [
    ("read", ["abc", '["note-a", "note-b"]', "start", "chat-1"]),
  ]


def test_remember_saves_one_fact_for_this_chat(recorded):
  service.dispatch(_request("/tools/remember", {"fact": "Prefers tea."}))
  assert recorded == [("remember", ["remember.py", "Prefers tea.", "chat-1"])]


@pytest.mark.parametrize("path, module_name, function_name, arguments", [
  ("/tools/search", "memory_search", "run", {"query": "prefs"}),
  ("/tools/read", "memory_read", "run", {"lookup_id": "abc", "selection": "all"}),
  ("/tools/remember", "remember", "main", {"fact": "Prefers tea."}),
])
def test_nonzero_tool_exit_preserves_failure_text_and_activity_receipt(
  monkeypatch, path, module_name, function_name, arguments,
):
  module = importlib.import_module(module_name)
  output = 'Could not complete.\nMOBIUS_APP_ACTIVITY_V1:{"status":"failed","detail":"disk unavailable"}\n'

  def failed(_args):
    print(output, end="")
    return 1

  monkeypatch.setattr(module, function_name, failed)
  response = service.dispatch(_request(path, arguments))
  assert response == {"status": 500, "body": output}


def test_nonzero_tool_exit_without_stdout_is_still_an_error():
  assert service._text_tool(lambda _args: 2, []) == {
    "status": 500, "body": {"detail": "Memory tool failed without output."},
  }


def test_tools_need_the_platforms_chat_identity(recorded):
  response = service.dispatch(_request("/tools/search", {"query": "x"}, call={}))
  assert response["status"] == 403
  assert recorded == []


@pytest.mark.parametrize("path, arguments", [
  ("/tools/search", {"query": " "}),
  ("/tools/read", {"selection": "all"}),
  ("/tools/remember", {}),
])
def test_malformed_arguments_are_tool_errors(recorded, path, arguments):
  assert service.dispatch(_request(path, arguments))["status"] == 422
  assert recorded == []
