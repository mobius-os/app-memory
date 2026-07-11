import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "memory_search.py"


def _load():
  spec = importlib.util.spec_from_file_location("app_memory_search", SCRIPT)
  module = importlib.util.module_from_spec(spec)
  assert spec.loader is not None
  spec.loader.exec_module(module)
  return module


class MemorySearchContractTests(unittest.TestCase):
  def test_returns_synthesis_only_with_verified_file_set(self):
    with tempfile.TemporaryDirectory() as raw:
      root = Path(raw) / "memory"
      (root / "notes").mkdir(parents=True)
      (root / ".ready").touch()
      (root / "notes" / "preference.md").write_text("fact", encoding="utf-8")
      module = _load()
      module.ROOT = root
      module._lookup = lambda _question: (
        0, "Prefers quiet UI.\nSOURCES: preference", "",
      )
      old_argv = sys.argv
      sys.argv = [str(SCRIPT), "Which UI preferences matter?"]
      out = io.StringIO()
      try:
        with contextlib.redirect_stdout(out):
          rc = module.run()
      finally:
        sys.argv = old_argv
      self.assertEqual(rc, 0)
      self.assertIn("Prefers quiet UI.", out.getvalue())
      self.assertIn("FILES: notes/preference.md", out.getvalue())

  def test_rejects_uncited_model_prose(self):
    with tempfile.TemporaryDirectory() as raw:
      root = Path(raw) / "memory"
      root.mkdir()
      (root / ".ready").touch()
      module = _load()
      module.ROOT = root
      module._lookup = lambda _question: (0, "Unsupported memory claim", "")
      old_argv = sys.argv
      sys.argv = [str(SCRIPT), "What matters?"]
      out = io.StringIO()
      err = io.StringIO()
      try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
          rc = module.run()
      finally:
        sys.argv = old_argv
      self.assertEqual(rc, 1)
      self.assertEqual(out.getvalue(), "")
      self.assertIn("no verifiable file pointers", err.getvalue())


if __name__ == "__main__":
  unittest.main()
