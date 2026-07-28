"""One confined text-only model boundary shared by Memory's agents."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile


CODEX_DISABLED_FEATURES = (
  "shell_tool", "unified_exec", "apps", "browser_use",
  "browser_use_external", "browser_use_full_cdp_access", "computer_use",
  "multi_agent", "image_generation", "goals",
)


def _codex_agent_text(stdout: str) -> str:
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


def available_provider(requested: str = "auto") -> str | None:
  requested = (requested or "auto").strip().lower()
  if requested in ("none", "off", "deterministic"):
    return None
  if requested == "claude":
    return "claude"
  if requested == "codex":
    return "codex"
  claude = os.environ.get("CLAUDE_CLI_PATH") or shutil.which("claude")
  claude_auth = os.environ.get("CLAUDE_CONFIG_DIR", "/data/cli-auth/claude")
  if claude and os.path.isdir(claude_auth):
    return "claude"
  codex = os.environ.get("CODEX_CLI_PATH") or shutil.which("codex")
  codex_home = os.environ.get("CODEX_HOME")
  if codex and codex_home and os.path.isdir(codex_home):
    return "codex"
  return None


def run_text(
  provider: str,
  prompt: str,
  *,
  model: object = None,
  effort: object = None,
  timeout: int = 90,
) -> str | None:
  """Return one model's text with tools, host authority, and cwd removed."""
  provider = (provider or "").strip().lower()
  if provider == "claude":
    executable = os.environ.get("CLAUDE_CLI_PATH") or shutil.which("claude")
    if not executable:
      return None
    env = {
      key: value for key, value in os.environ.items()
      if key in ("PATH", "HOME", "LANG", "LC_ALL", "CLAUDE_CONFIG_DIR")
    }
    cmd = [executable, "-p", "--tools", "", "--output-format", "text"]
    if model:
      cmd += ["--model", str(model)]
    normalized_effort = str(effort or "").strip().lower()
    if normalized_effort == "ultracode":
      normalized_effort = "xhigh"
    if normalized_effort in {"low", "medium", "high", "xhigh", "max"}:
      cmd += ["--effort", normalized_effort]
    parser = lambda stdout: stdout
  elif provider == "codex":
    executable = os.environ.get("CODEX_CLI_PATH") or shutil.which("codex")
    if not executable:
      return None
    env = {
      key: value for key, value in os.environ.items()
      if key in ("PATH", "HOME", "LANG", "LC_ALL", "CODEX_HOME")
    }
    cmd = [
      executable, "exec", "--json", "--ephemeral", "--ignore-user-config",
      "--ignore-rules", "--strict-config", "--skip-git-repo-check",
      "--sandbox", "read-only", "--color", "never",
    ]
    for feature in CODEX_DISABLED_FEATURES:
      cmd.extend(("--disable", feature))
    if model:
      cmd.extend(("--model", str(model)))
    normalized_effort = str(effort or "").strip().lower()
    if normalized_effort in {
      "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
    }:
      cmd.extend((
        "--config",
        f"model_reasoning_effort={json.dumps(normalized_effort)}",
      ))
    cmd.append("-")
    parser = _codex_agent_text
  else:
    return None

  try:
    with tempfile.TemporaryDirectory(prefix="memory-agent-") as cwd:
      proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
      )
  except (OSError, subprocess.TimeoutExpired):
    return None
  if proc.returncode != 0:
    return None
  value = parser(proc.stdout or "").strip()
  return value or None
