import io
import json
import urllib.error

import personalization_profile
from personalization_profile import build_profile, derive_confirmed, refresh_profile


class FakeResponse:
  def __init__(self, payload=None, *, etag=""):
    self.body = io.BytesIO(json.dumps(payload or {}).encode())
    self.headers = {"ETag": etag}

  def __enter__(self):
    return self

  def __exit__(self, *_args):
    return False

  def read(self, *args):
    return self.body.read(*args)


def test_confirmed_comes_only_from_about_the_user_notes():
  graph = {
    "nodes": [
      {
        "id": "yes",
        "title": "Yes",
        "description": "Evidence",
        "type": "note",
        "path": "notes/yes.md",
        "mocs": ["about-the-user"],
        "tags": ["user", "preference"],
      },
      {"id": "no", "title": "No", "type": "note", "mocs": ["other"]},
      {
        "id": "incident",
        "title": "Incident",
        "type": "note",
        "mocs": ["about-the-user"],
        "tags": ["tooling"],
      },
      {"id": "map", "title": "About", "type": "moc", "mocs": ["about-the-user"]},
    ],
  }
  assert [item["id"] for item in derive_confirmed(graph)] == ["yes"]


def test_refresh_preserves_explicit_owner_fields():
  profile = build_profile(
    {"nodes": []},
    {
      "priorities": [" Ship profile ", "Ship profile"],
      "boundaries": ["Never assume permission"],
      "hypotheses": ["Maybe concise"],
    },
    source_commit="abc",
  )
  assert profile["priorities"] == ["Ship profile"]
  assert profile["boundaries"] == ["Never assume permission"]
  assert profile["hypotheses"] == ["Maybe concise"]


def test_refresh_claims_first_creation_without_overwriting_a_racing_writer(monkeypatch):
  requests = []

  def urlopen(request, timeout):
    requests.append(request)
    if len(requests) == 1:
      raise urllib.error.HTTPError(request.full_url, 404, "missing", {}, None)
    return FakeResponse()

  monkeypatch.setattr(personalization_profile.urllib.request, "urlopen", urlopen)
  refresh_profile(
    api_base_url="https://example.test", token="token", app_id=7,
    graph={"nodes": []}, source_commit="abc",
  )
  assert requests[1].get_header("If-none-match") == "*"


def test_refresh_matches_the_profile_version_it_merged(monkeypatch):
  requests = []

  def urlopen(request, timeout):
    requests.append(request)
    if len(requests) == 1:
      return FakeResponse({"priorities": ["Keep this"]}, etag='"version-3"')
    return FakeResponse()

  monkeypatch.setattr(personalization_profile.urllib.request, "urlopen", urlopen)
  profile = refresh_profile(
    api_base_url="https://example.test", token="token", app_id=7,
    graph={"nodes": []}, source_commit="abc",
  )
  assert profile["priorities"] == ["Keep this"]
  assert requests[1].get_header("If-match") == '"version-3"'
