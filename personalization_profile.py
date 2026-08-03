"""Build and publish Memory's inspectable personalization profile."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime

PROFILE_PATH = "personalization-profile.json"
MAX_CONFIRMED, MAX_EXPLICIT = 48, 24


def _text(value: object, limit: int) -> str:
  return " ".join(str(value or "").split())[:limit]


def _explicit(items: object) -> list[str]:
  result: list[str] = []
  for item in items if isinstance(items, list) else []:
    value = _text(item, 300)
    if value and value not in result:
      result.append(value)
    if len(result) >= MAX_EXPLICIT:
      break
  return result


def derive_confirmed(graph: dict) -> list[dict]:
  """Return notes explicitly classified as user facts; never infer new ones."""
  result = []
  nodes = graph.get("nodes") if isinstance(graph, dict) else []
  for node in nodes if isinstance(nodes, list) else []:
    if not isinstance(node, dict) or node.get("type") == "moc":
      continue
    mocs = {
      str(value).rsplit("/", 1)[-1].removesuffix(".md")
      for value in (node.get("mocs") or [])
    }
    tags = {_text(tag, 80) for tag in (node.get("tags") or [])}
    if "about-the-user" not in mocs or "user" not in tags:
      continue
    result.append({
      "id": _text(node.get("id"), 160),
      "title": _text(node.get("title"), 200),
      "description": _text(node.get("description"), 500),
      "path": _text(node.get("path"), 240),
      "updated": _text(node.get("updated"), 80),
      "tags": [_text(tag, 80) for tag in (node.get("tags") or [])[:12]],
    })
  return sorted(
    result, key=lambda item: (item["title"].casefold(), item["id"]),
  )[:MAX_CONFIRMED]


def build_profile(
  graph: dict,
  previous: object = None,
  *,
  source_commit: str = "",
) -> dict:
  prior = previous if isinstance(previous, dict) else {}
  return {
    "schema": 1,
    "generated_at": datetime.now(UTC).isoformat(),
    "source_commit": _text(source_commit, 80),
    "confirmed": derive_confirmed(graph),
    "priorities": _explicit(prior.get("priorities")),
    "boundaries": _explicit(prior.get("boundaries")),
    "hypotheses": _explicit(prior.get("hypotheses")),
  }


def refresh_profile(
  *, api_base_url: str, token: str, app_id: int, graph: dict,
  source_commit: str,
) -> dict:
  """Merge owner-authored fields and PUT one canonical app-storage profile."""
  url = f"{api_base_url.rstrip('/')}/api/storage/apps/{app_id}/{PROFILE_PATH}"
  headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
  previous, etag = {}, ""
  try:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
      previous, etag = json.load(response), response.headers.get("ETag", "")
  except urllib.error.HTTPError as exc:
    if exc.code != 404:
      raise
  profile = build_profile(graph, previous, source_commit=source_commit)
  put_headers = {**headers, "Content-Type": "application/json"}
  if etag:
    put_headers["If-Match"] = etag
  else:
    put_headers["If-None-Match"] = "*"
  request = urllib.request.Request(
    url,
    data=json.dumps(
      profile, ensure_ascii=False, separators=(",", ":"),
    ).encode(),
    headers=put_headers,
    method="PUT",
  )
  with urllib.request.urlopen(request, timeout=20):
    pass
  return profile
