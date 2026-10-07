"""The JSON-v1 entry stays safe for Möbius to preload."""

import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ServicePreloadTests(unittest.TestCase):
  def test_preloaded_module_setup_needs_no_request_credential_and_starts_no_threads(self):
    # MOBIUS_PRELOAD lets Möbius run module setup once, without APP_TOKEN, and
    # fork each request from it; the per-request block must stay last.
    tree = ast.parse((ROOT / "service.py").read_text())
    self.assertEqual(ast.unparse(tree.body[-1].test), "__name__ == '__main__'")
    self.assertIn("MOBIUS_PRELOAD = True", [ast.unparse(node) for node in tree.body])
    with tempfile.TemporaryDirectory() as storage:
      env = {key: value for key, value in os.environ.items() if key != "APP_TOKEN"}
      env.update(APP_ID="7", APP_SLUG="memory", APP_STORAGE_DIR=storage)
      probe = subprocess.run(
        [sys.executable, "-c",
         "import sys, threading, service; assert service.MOBIUS_PRELOAD is True; "
         "assert threading.active_count() == 1, threading.enumerate(); "
         # Forked requests must not re-import the tool modules.
         "assert {'memory_search', 'memory_read', 'remember'} <= set(sys.modules)"],
        cwd=ROOT, env=env, text=True, capture_output=True,
      )
    self.assertEqual(probe.returncode, 0, probe.stderr)


if __name__ == "__main__":
  unittest.main()
