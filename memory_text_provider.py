"""Shared confined text-only provider boundaries for Memory's two agents."""

from __future__ import annotations

import json
import os
import shutil


CODEX_DISABLED_FEATURES = (
  "shell_tool", "unified_exec", "apps", "browser_use",
  "browser_use_external", "browser_use_full_cdp_access", "computer_use",
  "multi_agent", "image_generation", "goals",
)


def codex_cli_path() -> str | None:
  return os.environ.get("CODEX_CLI_PATH") or shutil.which("codex")


def codex_environment() -> dict[str, str]:
  """The only host environment a confined Codex selector may inherit."""
  return {
    key: value for key, value in os.environ.items()
    if key in ("PATH", "HOME", "LANG", "LC_ALL", "CODEX_HOME")
  }


def codex_text_command(
  *, model: object = None, effort: object = None,
) -> list[str] | None:
  """Build Memory's single reviewed, read-only Codex invocation."""
  codex = codex_cli_path()
  if not codex:
    return None
  cmd = [
    codex, "exec", "--json", "--ephemeral", "--ignore-user-config",
    "--ignore-rules", "--strict-config", "--skip-git-repo-check",
    "--sandbox", "read-only", "--color", "never",
  ]
  for feature in CODEX_DISABLED_FEATURES:
    cmd.extend(("--disable", feature))
  if model:
    cmd.extend(("--model", str(model)))
  if effort in ("none", "minimal", "low", "medium", "high", "xhigh"):
    cmd.extend(("--config", f"model_reasoning_effort={json.dumps(effort)}"))
  cmd.append("-")
  return cmd


def codex_agent_text(stdout: str) -> str:
  """Concatenate only agent-message text from a Codex JSON event stream."""
  parts: list[str] = []
  for raw_line in stdout.splitlines():
    try:
      event = json.loads(raw_line)
    except (TypeError, ValueError):
      continue
    if event.get("type") not in ("item.completed", "agent_message"):
      continue
    item = event.get("item") if isinstance(event.get("item"), dict) else event
    if item.get("type") not in ("agent_message", "agentMessage"):
      continue
    value = item.get("text") or item.get("content")
    if isinstance(value, str) and value:
      parts.append(value)
  return "".join(parts)
