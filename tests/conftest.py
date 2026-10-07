"""Keep every Memory test away from a live Möbius instance.

The modules read DATA_DIR, APP_JOB_STATE_DIR, and API_BASE_URL at import, and
pytest imports this file before any test module. Pointing them at a throwaway
directory and an unreachable address means a test that forgets a stub fails
here the same way it fails in CI, instead of silently reading or writing the
owner's real Memory state or calling the running server.
"""

import os
import stat
import tempfile

import pytest

_ROOT = tempfile.mkdtemp(prefix="memory-tests-")
os.environ["DATA_DIR"] = os.path.join(_ROOT, "data")
os.environ["APP_JOB_STATE_DIR"] = os.path.join(_ROOT, "job-state")
os.environ["API_BASE_URL"] = "http://127.0.0.1:9"

# No test may reach a real provider CLI: a live `claude`/`codex` call costs
# money and makes results depend on a model. Recall defaults to the
# deterministic reader, provider logins point at empty directories, and both
# CLI names resolve to a stub that records the attempt and fails. A test that
# needs provider behavior must stub `run_text`/`subprocess` itself; any stub
# invocation fails the session so a leak is visible instead of silently paid.
_STUB_BIN = os.path.join(_ROOT, "provider-stub-bin")
_STUB_LOG = os.path.join(_ROOT, "provider-stub-calls.log")
os.makedirs(_STUB_BIN)
for _name in ("claude", "codex"):
  _stub = os.path.join(_STUB_BIN, _name)
  with open(_stub, "w", encoding="utf-8") as handle:
    handle.write(f'#!/bin/sh\necho "{_name}" >> "{_STUB_LOG}"\nexit 127\n')
  os.chmod(_stub, stat.S_IRWXU)
os.environ["PATH"] = _STUB_BIN + os.pathsep + os.environ.get("PATH", "")
os.environ["CLAUDE_CLI_PATH"] = os.path.join(_STUB_BIN, "claude")
os.environ["CODEX_CLI_PATH"] = os.path.join(_STUB_BIN, "codex")
os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(_ROOT, "no-claude-login")
os.environ["CODEX_HOME"] = os.path.join(_ROOT, "no-codex-login")
os.environ["MEMORY_READER_PROVIDER"] = "none"


def pytest_sessionfinish(session, exitstatus):
  if os.path.exists(_STUB_LOG):
    with open(_STUB_LOG, encoding="utf-8") as handle:
      calls = handle.read().split()
    session.config.pluginmanager.get_plugin("terminalreporter").write_line(
      f"FAILED: tests invoked a provider CLI {len(calls)} time(s): {sorted(set(calls))}",
      red=True,
    )
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
