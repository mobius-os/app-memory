"""One confined text-only model boundary shared by Memory's agents."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass


CODEX_DISABLED_FEATURES = (
  "shell_tool", "unified_exec", "apps", "browser_use",
  "browser_use_external", "browser_use_full_cdp_access", "computer_use",
  "multi_agent", "image_generation", "goals",
)


@dataclass(frozen=True)
class ProviderFailure:
  """A provider failure with the lifetime of the decision it supports."""

  code: str
  terminal: bool = False
  scope: str = "attempt"


@dataclass(frozen=True)
class TextResult:
  text: str | None
  failure: ProviderFailure | None = None


class RunProviderHealth:
  """Remember only terminal failures for one Memory operation or night."""

  def __init__(self) -> None:
    self._providers: dict[str, ProviderFailure] = {}
    self._choices: dict[tuple[str, str], ProviderFailure] = {}

  def unavailable(
    self, provider: str, model: object = None,
  ) -> ProviderFailure | None:
    provider = str(provider or "").strip().lower()
    model_key = str(model or "").strip()
    return self._providers.get(provider) or self._choices.get((provider, model_key))

  def observe(
    self,
    provider: str,
    model: object,
    failure: ProviderFailure | None,
  ) -> bool:
    """Record a terminal failure; return True only when health changed."""
    if failure is None or not failure.terminal:
      return False
    provider = str(provider or "").strip().lower()
    model_key = str(model or "").strip()
    if failure.scope == "provider":
      if provider in self._providers:
        return False
      self._providers[provider] = failure
      return True
    key = (provider, model_key)
    if key in self._choices:
      return False
    self._choices[key] = failure
    return True


_TERMINAL_FAILURES = (
  (
    "usage_limit",
    "provider",
    re.compile(
      r"usage limit|spend limit|monthly limit|insufficient credits?|"
      r"credit balance|quota exceeded",
      re.I,
    ),
  ),
  (
    "authentication",
    "provider",
    re.compile(
      r"authentication failed|not logged in|unauthori[sz]ed|invalid api key|"
      r"invalid_api_key|please (?:log in|login)|run /login",
      re.I,
    ),
  ),
  (
    "model_unavailable",
    "choice",
    re.compile(
      r"model (?:is )?not found|unknown model|unsupported model|"
      r"model .* does not exist|do not have access to (?:the )?model",
      re.I,
    ),
  ),
)


def classify_process_failure(
  returncode: int,
  stdout: str = "",
  stderr: str = "",
) -> ProviderFailure:
  """Classify a failed confined CLI without treating transients as terminal."""
  evidence = f"{stderr}\n{stdout}"[-24_000:]
  for code, scope, pattern in _TERMINAL_FAILURES:
    if pattern.search(evidence):
      return ProviderFailure(code, terminal=True, scope=scope)
  return ProviderFailure(f"process_exit_{returncode}")


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
) -> TextResult:
  """Return confined text plus a typed failure suitable for run-scoped health."""
  provider = (provider or "").strip().lower()
  if provider == "claude":
    executable = os.environ.get("CLAUDE_CLI_PATH") or shutil.which("claude")
    if not executable:
      return TextResult(
        None, ProviderFailure("provider_unavailable", True, "provider"),
      )
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
      return TextResult(
        None, ProviderFailure("provider_unavailable", True, "provider"),
      )
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
    return TextResult(
      None, ProviderFailure("unsupported_provider", True, "provider"),
    )

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
  except subprocess.TimeoutExpired:
    return TextResult(None, ProviderFailure("timeout"))
  except OSError:
    return TextResult(
      None, ProviderFailure("provider_unavailable", True, "provider"),
    )
  if proc.returncode != 0:
    return TextResult(
      None,
      classify_process_failure(
        proc.returncode, proc.stdout or "", proc.stderr or "",
      ),
    )
  value = parser(proc.stdout or "").strip()
  if not value:
    return TextResult(None, ProviderFailure("empty_output"))
  return TextResult(value)
