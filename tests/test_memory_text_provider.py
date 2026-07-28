import json
import os
import unittest
from unittest import mock

import memory_text_provider as provider


class MemoryTextProviderTests(unittest.TestCase):
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
    result = mock.Mock(returncode=0, stdout=stream)
    with (
      mock.patch.dict(os.environ, {
        "CODEX_CLI_PATH": "/usr/bin/codex",
        "CODEX_HOME": "/tmp/codex-home",
        "AGENT_TOKEN": "owner-secret",
        "APP_TOKEN": "app-secret",
      }, clear=True),
      mock.patch.object(provider.subprocess, "run", return_value=result) as run,
    ):
      text = provider.run_text(
        "codex", "choose notes", model="gpt-test", effort="high",
      )

    self.assertEqual(text, "first second")
    command = run.call_args.args[0]
    self.assertIn("read-only", command)
    self.assertEqual(command[-1], "-")
    self.assertIn("gpt-test", command)
    self.assertIn('model_reasoning_effort="high"', command)
    for feature in provider.CODEX_DISABLED_FEATURES:
      self.assertIn(feature, command)
    self.assertEqual(run.call_args.kwargs["input"], "choose notes")
    self.assertEqual(run.call_args.kwargs["env"], {
      "CODEX_HOME": "/tmp/codex-home",
    })

  def test_claude_run_is_tool_free_and_excludes_app_credentials(self):
    result = mock.Mock(returncode=0, stdout="answer")
    with (
      mock.patch.dict(os.environ, {
        "CLAUDE_CLI_PATH": "/usr/bin/claude",
        "CLAUDE_CONFIG_DIR": "/tmp/claude-home",
        "AGENT_TOKEN": "owner-secret",
        "APP_TOKEN": "app-secret",
      }, clear=True),
      mock.patch.object(provider.subprocess, "run", return_value=result) as run,
    ):
      text = provider.run_text("claude", "navigate", effort="high")

    self.assertEqual(text, "answer")
    command = run.call_args.args[0]
    self.assertIn("--tools", command)
    self.assertEqual(command[command.index("--tools") + 1], "")
    self.assertIn("--effort", command)
    self.assertNotIn("AGENT_TOKEN", run.call_args.kwargs["env"])
    self.assertNotIn("APP_TOKEN", run.call_args.kwargs["env"])

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


if __name__ == "__main__":
  unittest.main()
