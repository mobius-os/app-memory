import json

from model_selection import RETIRED_MODEL_IDS, load_settings, migrate_agent_models


def test_migrates_exact_retired_ids_and_preserves_unknowns():
  for retired, current in RETIRED_MODEL_IDS.items():
    value = {"model": retired, "fallback_model": retired, "keep": 7}
    migrated, changed = migrate_agent_models(value)
    assert changed
    assert migrated == {"model": current, "fallback_model": current, "keep": 7}
    assert migrate_agent_models(migrated) == (migrated, False)
  unknown = {"model": "future-model", "fallback_model": "gpt-5.5"}
  assert migrate_agent_models(unknown) == (unknown, False)


def test_load_settings_replaces_atomically_and_only_once(tmp_path, monkeypatch):
  path = tmp_path / "settings.json"
  path.write_text(json.dumps({"model": "claude-opus-4-6-20251015", "keep": True}))
  calls = []
  from model_selection import os as model_os
  real_replace = model_os.replace
  monkeypatch.setattr(model_os, "replace", lambda source, target: (calls.append((source, target)), real_replace(source, target))[1])
  assert load_settings(path)["model"] == "claude-opus-4-6"
  assert len(calls) == 1
  assert load_settings(path)["model"] == "claude-opus-4-6"
  assert len(calls) == 1
