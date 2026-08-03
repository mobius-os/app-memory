import json

import personalization_profile
from personalization_profile import derive_confirmed, refresh_profile


class FakeResponse:
  def __enter__(self):
    return self

  def __exit__(self, *_args):
    return False


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
  assert derive_confirmed(graph) == [{
    "id": "yes",
    "title": "Yes",
    "description": "Evidence",
    "path": "notes/yes.md",
    "updated": "",
  }]


def test_refresh_publishes_only_confirmed_context_and_provenance(monkeypatch):
  requests = []

  def urlopen(request, timeout):
    requests.append(request)
    return FakeResponse()

  monkeypatch.setattr(personalization_profile.urllib.request, "urlopen", urlopen)
  refresh_profile(
    api_base_url="https://example.test", token="token", app_id=7,
    graph={"nodes": []},
    source_commit="abc",
  )
  assert len(requests) == 1
  assert requests[0].get_method() == "PUT"
  payload = json.loads(requests[0].data)
  assert set(payload) == {"schema", "generated_at", "source_commit", "confirmed"}
  assert payload["source_commit"] == "abc"
  assert payload["confirmed"] == []
