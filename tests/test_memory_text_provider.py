import json
import os
import unittest
from unittest import mock

import memory_text_provider as provider


class MemoryTextProviderTests(unittest.TestCase):
  @staticmethod
  def _popen(stdout, capture):
    class FakePopen:
      pid = 999999
      returncode = 0

      def __init__(self, cmd, **kwargs):
        capture.update({"cmd": cmd, **kwargs})

      def communicate(self, value=None, timeout=None):
        capture["input"] = value
        capture["timeout"] = timeout
        return stdout, ""

    return FakePopen

  def test_codex_run_has_one_confined_feature_and_environment_contract(self):
    stream = "\n".join([
      json.dumps({"type": "item.completed", "item": {
        "type": "command_execution", "text": "ignore",
      }}),
      "not json",
      json.dumps({"type": "item.completed", "item": {
        "type": "agent_message", "text": "first",
      }}),
      json.dumps({"type": "agent_message", "content": " second"}),
    ])
    captured = {}
    with (
      mock.patch.dict(os.environ, {
        "CODEX_CLI_PATH": "/usr/bin/codex",
        "CODEX_HOME": "/tmp/codex-home",
        "AGENT_TOKEN": "owner-secret",
        "APP_TOKEN": "app-secret",
      }, clear=True),
      mock.patch.object(
        provider.subprocess, "Popen", self._popen(stream, captured),
      ),
    ):
      text = provider.run_text(
        "codex", "choose notes", model="gpt-test", effort="high",
      )

    self.assertEqual(text.text, "first second")
    self.assertIsNone(text.failure)
    command = captured["cmd"]
    self.assertIn("read-only", command)
    self.assertEqual(command[-1], "-")
    self.assertIn("gpt-test", command)
    self.assertIn('model_reasoning_effort="high"', command)
    for feature in provider.CODEX_DISABLED_FEATURES:
      self.assertIn(feature, command)
    self.assertEqual(captured["input"], "choose notes")
    self.assertEqual(captured["env"], {
      "CODEX_HOME": "/tmp/codex-home",
    })

  def test_claude_run_is_tool_free_and_excludes_app_credentials(self):
    captured = {}
    with (
      mock.patch.dict(os.environ, {
        "CLAUDE_CLI_PATH": "/usr/bin/claude",
        "CLAUDE_CONFIG_DIR": "/tmp/claude-home",
        "AGENT_TOKEN": "owner-secret",
        "APP_TOKEN": "app-secret",
      }, clear=True),
      mock.patch.object(
        provider.subprocess, "Popen", self._popen("answer", captured),
      ),
    ):
      text = provider.run_text("claude", "navigate", effort="high")

    self.assertEqual(text.text, "answer")
    self.assertIsNone(text.failure)
    command = captured["cmd"]
    self.assertIn("--tools", command)
    self.assertEqual(command[command.index("--tools") + 1], "")
    self.assertIn("--effort", command)
    self.assertNotIn("AGENT_TOKEN", captured["env"])
    self.assertNotIn("APP_TOKEN", captured["env"])

  def test_timeout_kills_and_reaps_the_whole_provider_session(self):
    captured = {}

    class TimedOutPopen:
      pid = 123456
      returncode = -9
      calls = 0

      def __init__(self, cmd, **kwargs):
        captured.update({"cmd": cmd, **kwargs})

      def communicate(self, value=None, timeout=None):
        self.calls += 1
        if self.calls == 1:
          raise provider.subprocess.TimeoutExpired("agent", timeout)
        return "", ""

    with (
      mock.patch.dict(os.environ, {"CLAUDE_CLI_PATH": "/usr/bin/claude"}, clear=True),
      mock.patch.object(provider.subprocess, "Popen", TimedOutPopen),
      mock.patch.object(provider.os, "killpg") as killpg,
    ):
      result = provider.run_text("claude", "prompt", timeout=1)

    self.assertEqual(result.failure.code, "timeout")
    killpg.assert_called_once_with(123456, provider.signal.SIGKILL)
    self.assertEqual(provider._ACTIVE_PROCESS_GROUPS, set())

  def test_claude_effort_allows_reviewed_values_and_omits_unknown_values(self):
    commands = []

    class FakePopen:
      pid = 999998
      returncode = 0

      def __init__(self, cmd, **_kwargs):
        commands.append(cmd)

      def communicate(self, value=None, timeout=None):
        return '{"updates":[]}', ""

    with (
      mock.patch.dict(os.environ, {"CLAUDE_CLI_PATH": "/usr/bin/claude"}, clear=True),
      mock.patch.object(provider.subprocess, "Popen", FakePopen),
    ):
      provider.run_text("claude", "prompt", effort="ultracode")
      provider.run_text("claude", "prompt", effort="future-level")

    self.assertEqual(commands[0][commands[0].index("--effort") + 1], "xhigh")
    self.assertNotIn("--effort", commands[1])

  def test_shutdown_terminates_every_active_provider_session(self):
    provider._ACTIVE_PROCESS_GROUPS.update({123456, 234567})
    try:
      with mock.patch.object(provider, "_kill_process_group") as kill:
        provider.terminate_active_text_processes()
      self.assertEqual(
        {call.args[0] for call in kill.call_args_list},
        {123456, 234567},
      )
    finally:
      provider._ACTIVE_PROCESS_GROUPS.clear()

  def test_auto_provider_prefers_available_claude_then_codex(self):
    with (
      mock.patch.object(provider.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"),
      mock.patch.object(provider.os.path, "isdir", side_effect=lambda path: path in {
        "/tmp/claude", "/tmp/codex",
      }),
      mock.patch.dict(os.environ, {
        "CLAUDE_CONFIG_DIR": "/tmp/claude",
        "CODEX_HOME": "/tmp/codex",
      }, clear=True),
    ):
      self.assertEqual(provider.available_provider("auto"), "claude")

    with (
      mock.patch.object(provider.shutil, "which", side_effect=lambda name: (
        None if name == "claude" else "/usr/bin/codex"
      )),
      mock.patch.object(provider.os.path, "isdir", return_value=True),
      mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/codex"}, clear=True),
    ):
      self.assertEqual(provider.available_provider("auto"), "codex")


  def test_json_object_survives_the_prose_models_wrap_their_answer_in(self):
    expected = {"summary": "s", "updates": [{"path": "notes/a.md"}]}
    body = json.dumps(expected)
    for label, reply in (
      ("bare", body),
      ("fenced", f"```json\n{body}\n```"),
      ("lead-in", f"Here is the JSON object you asked for:\n\n{body}"),
      ("trailing remark", f"{body}\n\nLet me know if you want changes."),
      ("both", f"Sure - here it is:\n```json\n{body}\n```\nHope that helps!"),
    ):
      with self.subTest(label):
        self.assertEqual(provider.json_object(reply), expected)

  def test_json_object_reads_past_braces_inside_string_values(self):
    value = {"reason": "a } and a { inside prose", "nested": {"deep": [1, 2]}}
    self.assertEqual(provider.json_object(json.dumps(value)), value)

  def test_json_object_rejects_replies_with_no_recoverable_object(self):
    for label, reply in (
      ("truncated mid-token", '{"summary": "s", "updates": [{"path": "notes/'),
      ("no object at all", "I could not complete this request."),
      ("top-level array", "[1, 2, 3]"),
      ("empty", ""),
      ("not a string", None),
    ):
      with self.subTest(label):
        self.assertIsNone(provider.json_object(reply))


if __name__ == "__main__":
  unittest.main()
