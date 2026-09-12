"""Atomic one-way migration for Memory's persisted agent models."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

RETIRED_MODEL_IDS = {
  "claude-opus-4-5-20251001": "claude-opus-4-5-20251101",
  "claude-sonnet-4-5-20251001": "claude-sonnet-4-5-20250929",
  "claude-opus-4-6-20251015": "claude-opus-4-6",
  "claude-opus-4-7-20251215": "claude-opus-4-7",
  "claude-sonnet-4-7-20251215": "claude-sonnet-4-6",
}


def migrate_agent_models(settings: object) -> tuple[object, bool]:
  if not isinstance(settings, dict):
    return settings, False
  migrated = dict(settings)
  changed = False
  for key in ("model", "fallback_model"):
    replacement = RETIRED_MODEL_IDS.get(settings.get(key))
    if replacement:
      migrated[key] = replacement
      changed = True
  return (migrated, True) if changed else (settings, False)


def load_settings(path: Path) -> dict:
  try:
    value = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return {}
  if not isinstance(value, dict):
    return {}
  migrated, changed = migrate_agent_models(value)
  if changed:
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
      with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(migrated, handle, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
      os.replace(tmp, path)
    finally:
      tmp.unlink(missing_ok=True)
  return migrated
