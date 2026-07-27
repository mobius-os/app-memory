import json
import os
import unittest
from unittest import mock

import memory_text_provider as provider


class MemoryTextProviderTests(unittest.TestCase):
  def test_codex_command_has_one_confined_feature_contract(self):
    with mock.patch.dict(os.environ, {"CODEX_CLI_PATH": "/usr/bin/codex"}):
      command = provider.codex_text_command(model="gpt-test", effort="high")

    self.assertIsNotNone(command)
    self.assertIn("read-only", command)
    self.assertEqual(command[-1], "-")
    self.assertIn("gpt-test", command)
    self.assertIn('model_reasoning_effort="high"', command)
    for feature in provider.CODEX_DISABLED_FEATURES:
      self.assertIn(feature, command)

  def test_codex_environment_excludes_platform_and_app_credentials(self):
    with mock.patch.dict(os.environ, {
      "CODEX_HOME": "/tmp/codex-home",
      "AGENT_TOKEN": "owner-secret",
      "APP_TOKEN": "app-secret",
    }, clear=True):
      environment = provider.codex_environment()

    self.assertEqual(environment, {"CODEX_HOME": "/tmp/codex-home"})

  def test_codex_event_parser_keeps_only_agent_messages(self):
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

    self.assertEqual(provider.codex_agent_text(stream), "first second")


if __name__ == "__main__":
  unittest.main()
