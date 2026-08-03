"""Build and publish Memory's bounded context for Reflection."""

from __future__ import annotations

import json
import urllib.request
from datetime import UTC, datetime

PROFILE_PATH = "personalization-profile.json"
MAX_CONFIRMED = 48


def _text(value: object, limit: int) -> str:
  if not isinstance(value, str):
    return ""
  return " ".join(value.split())[:limit]


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
    })
  return sorted(
    result, key=lambda item: (item["title"].casefold(), item["id"]),
  )[:MAX_CONFIRMED]


def build_profile(
  graph: dict,
  *,
  source_commit: str = "",
) -> dict:
  return {
    "schema": 1,
    "generated_at": datetime.now(UTC).isoformat(),
    "source_commit": _text(source_commit, 80),
    "confirmed": derive_confirmed(graph),
  }


def refresh_profile(
  *, api_base_url: str, token: str, app_id: int, graph: dict,
  source_commit: str,
) -> dict:
  """Publish one bounded, evidence-backed profile for Reflection."""
  url = f"{api_base_url.rstrip('/')}/api/storage/apps/{app_id}/{PROFILE_PATH}"
  profile = build_profile(graph, source_commit=source_commit)
  request = urllib.request.Request(
    url,
    data=json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode(),
    headers={
      "Authorization": f"Bearer {token}",
      "Content-Type": "application/json",
    },
    method="PUT",
  )
  with urllib.request.urlopen(request, timeout=20):
    pass
  return profile
