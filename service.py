#!/usr/bin/env python3
"""Memory's agent tools, served through the platform's JSON-v1 app service.

The platform calls ``POST /tools/<name>`` with ``{"arguments": ..., "call":
...}``. ``call`` is the platform-authenticated moment of the agent's tool call;
its ``chat_id`` scopes the lookup, so an agent can never read another chat's
lookup by naming it. Each tool returns the same text the recall scripts print,
ending with the activity receipt line the chat renders as a Memory card.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from collections.abc import Callable

# Imported here, not per request: Möbius preloads this module and forks each
# request from it, so these imports (about 50 ms) are paid once. They read only
# per-installation environment values at import time and start no threads.
import memory_read
import memory_search
import remember


def _text_tool(run: Callable[[list[str]], int], args: list[str]) -> dict:
  output = io.StringIO()
  with contextlib.redirect_stdout(output):
    exit_code = run(args)
  body = output.getvalue()
  if exit_code != 0:
    return {"status": 500, "body": body or {"detail": "Memory tool failed without output."}}
  return {"status": 200, "body": body}


def _search(arguments: dict, chat_id: str) -> dict:
  query = arguments.get("query")
  if not isinstance(query, str) or not query.strip():
    return {"status": 422, "body": {"detail": "query must describe the context needed."}}
  return _text_tool(memory_search.run, [query, chat_id])


def _read(arguments: dict, chat_id: str) -> dict:
  lookup_id = arguments.get("lookup_id")
  selection = arguments.get("selection")
  cursor = arguments.get("cursor", "start")
  if isinstance(selection, list):
    selection = json.dumps(selection)
  if not all(isinstance(value, str) and value for value in (lookup_id, selection, cursor)):
    return {"status": 422, "body": {
      "detail": "read needs lookup_id, selection, and optionally cursor.",
    }}
  return _text_tool(memory_read.run, [lookup_id, selection, cursor, chat_id])


def _remember(arguments: dict, chat_id: str) -> dict:
  fact = arguments.get("fact")
  if not isinstance(fact, str) or not fact.strip():
    return {"status": 422, "body": {"detail": "fact must be one self-contained fact."}}
  return _text_tool(remember.main, ["remember.py", fact, chat_id])


TOOLS = {
  "/tools/search": _search,
  "/tools/read": _read,
  "/tools/remember": _remember,
}


def dispatch(request: dict) -> dict:
  handler = TOOLS.get(request.get("path"))
  if handler is None or request.get("method") != "POST":
    return {"status": 404, "body": {"detail": "Not found."}}
  body = request.get("body") if isinstance(request.get("body"), dict) else {}
  call = body.get("call") if isinstance(body.get("call"), dict) else {}
  arguments = body.get("arguments") if isinstance(body.get("arguments"), dict) else {}
  chat_id = call.get("chat_id")
  if not isinstance(chat_id, str) or not chat_id:
    return {"status": 403, "body": {"detail": "Memory tools run only inside an agent chat."}}
  # The recall cache distinguishes physical runs by this identity.
  if isinstance(call.get("run_id"), str):
    os.environ["MOBIUS_RUN_TOKEN"] = call["run_id"]
  else:
    os.environ.pop("MOBIUS_RUN_TOKEN", None)
  return handler(arguments, chat_id)


# Möbius may run everything above once and fork each request from it,
# removing interpreter start-up and imports from every request. Module setup
# therefore reads only per-installation values and starts no threads.
MOBIUS_PRELOAD = True

if __name__ == "__main__":
  request = json.loads(sys.stdin.read())
  print(json.dumps(dispatch(request), ensure_ascii=False, separators=(",", ":")))
