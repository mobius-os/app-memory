"""One confined text-only model boundary shared by Memory's agents."""

from __future__ import annotations

import json
import os
import re
import signal
import shutil
import subprocess
import tempfile
from dataclasses import dataclass


CODEX_DISABLED_FEATURES = (
  "shell_tool", "unified_exec", "apps", "browser_use",
  "browser_use_external", "browser_use_full_cdp_access", "computer_use",
  "multi_agent", "image_generation", "goals",
)
_ACTIVE_PROCESS_GROUPS: set[int] = set()


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
  receipt: dict | None = None


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


def _kill_process_group(pid: int) -> None:
  try:
    os.killpg(pid, signal.SIGKILL)
  except ProcessLookupError:
    pass


def terminate_active_text_processes() -> None:
  """Reap every confined provider session owned by this process."""
  for pid in tuple(_ACTIVE_PROCESS_GROUPS):
    _kill_process_group(pid)


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


def _numeric_usage(value: object) -> dict | None:
  if not isinstance(value, dict):
    return None
  result = {
    str(key)[:80]: item
    for key, item in list(value.items())[:40]
    if isinstance(item, (int, float)) and not isinstance(item, bool)
  }
  return result or None


def _codex_usage(stdout: str) -> dict | None:
  for raw_line in reversed(stdout.splitlines()):
    try:
      event = json.loads(raw_line)
    except (TypeError, ValueError):
      continue
    if event.get("type") == "turn.completed":
      return _numeric_usage(event.get("usage"))
  return None


def _claude_result(stdout: str) -> tuple[str, dict | None, float | None] | None:
  try:
    payload = json.loads(stdout)
  except (TypeError, ValueError):
    return None
  if not isinstance(payload, dict) or not isinstance(payload.get("result"), str):
    return None
  cost = payload.get("total_cost_usd")
  if not isinstance(cost, (int, float)) or isinstance(cost, bool):
    cost = None
  return payload["result"], _numeric_usage(payload.get("usage")), cost


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
    cmd = [executable, "-p", "--tools", "", "--output-format", "json"]
    if model:
      cmd += ["--model", str(model)]
    normalized_effort = str(effort or "").strip().lower()
    if normalized_effort == "ultracode":
      normalized_effort = "xhigh"
    if normalized_effort in {"low", "medium", "high", "xhigh", "max"}:
      cmd += ["--effort", normalized_effort]
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
  else:
    return TextResult(
      None, ProviderFailure("unsupported_provider", True, "provider"),
    )

  try:
    with tempfile.TemporaryDirectory(prefix="memory-agent-") as cwd:
      proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
      )
      _ACTIVE_PROCESS_GROUPS.add(proc.pid)
      try:
        try:
          stdout, stderr = proc.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
          _kill_process_group(proc.pid)
          proc.communicate()
          return TextResult(None, ProviderFailure("timeout"))
      finally:
        _ACTIVE_PROCESS_GROUPS.discard(proc.pid)
  except OSError:
    return TextResult(
      None, ProviderFailure("provider_unavailable", True, "provider"),
    )
  if proc.returncode != 0:
    return TextResult(
      None,
      classify_process_failure(
        proc.returncode, stdout or "", stderr or "",
      ),
    )
  usage = None
  cost_usd = None
  if provider == "claude":
    parsed = _claude_result(stdout or "")
    if parsed is None:
      return TextResult(None, ProviderFailure("invalid_output"))
    value, usage, cost_usd = parsed
  else:
    value = _codex_agent_text(stdout or "")
    usage = _codex_usage(stdout or "")
  value = value.strip()
  if not value:
    return TextResult(None, ProviderFailure("empty_output"))
  return TextResult(value, receipt={
    "input_chars": len(prompt),
    "output_chars": len(value),
    "usage": usage,
    "cost_usd": cost_usd,
  })


def json_object(text: str | None) -> dict | None:
  """Return the first JSON object in a model reply, ignoring prose around it.

  Every Memory agent asks a model for one JSON object and gets back a reply that
  may carry a lead-in sentence, a code fence, or a trailing remark. Requiring the
  whole reply to parse discarded otherwise-good work, and the two callers had
  each grown their own copy of the same brittle check. `raw_decode` consumes
  exactly one complete object and ignores whatever surrounds it, so only a
  genuinely incomplete reply now fails - the one case worth reporting.
  """
  if not isinstance(text, str):
    return None
  start = text.find("{")
  if start < 0:
    return None
  try:
    value, _ = json.JSONDecoder().raw_decode(text, start)
  except ValueError:
    return None
  return value if isinstance(value, dict) else None
