import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]


def _load(data_dir: Path):
  sys.modules.pop("memory_store", None)
  sys.path.insert(0, str(REPO))
  try:
    with mock.patch.dict(os.environ, {"DATA_DIR": str(data_dir)}):
      return importlib.import_module("memory_store")
  finally:
    sys.path.remove(str(REPO))


def _seed(root: Path):
  (root / "notes").mkdir(parents=True)
  (root / "mocs").mkdir()
  (root / "index.md").write_text("# Memory\n", encoding="utf-8")


class MemoryStoreTests(unittest.TestCase):
  def test_failed_or_discarded_stage_leaves_old_pointer_readable(self):
    with tempfile.TemporaryDirectory() as raw:
      store = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      _, first = store.start_staging(seed)
      (first / "graph.json").write_text('{"nodes":[]}', encoding="utf-8")
      pointer = store.publish(first)
      _, second = store.start_staging(seed)
      (second / "notes" / "partial.md").write_text("partial", encoding="utf-8")

      store.discard_staging(second)

      self.assertEqual(store.ready_pointer()["generation"], pointer["generation"])
      self.assertEqual(store.read_generation_file(pointer["generation"], "graph.json"), '{"nodes":[]}')

  def test_publish_is_complete_before_pointer_advances(self):
    with tempfile.TemporaryDirectory() as raw:
      store = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      _, staging = store.start_staging(seed)
      (staging / "notes" / "fact.md").write_text("durable fact", encoding="utf-8")
      (staging / "graph.json").write_text('{"nodes":[]}', encoding="utf-8")

      pointer = store.publish(staging)

      self.assertEqual(json.loads(store.READY.read_text()), pointer)
      self.assertEqual(
        store.read_generation_file(pointer["generation"], "notes/fact.md"),
        "durable fact",
      )
      self.assertFalse(any(store.GENERATIONS.glob(".staging-*")))

  def test_publish_rejects_symlink_without_advancing_pointer(self):
    with tempfile.TemporaryDirectory() as raw:
      store = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      _, first = store.start_staging(seed)
      (first / "graph.json").write_text('{"nodes":[]}', encoding="utf-8")
      pointer = store.publish(first)
      _, unsafe = store.start_staging(seed)
      outside = Path(raw) / "outside"
      outside.write_text("secret", encoding="utf-8")
      (unsafe / "notes" / "escape.md").symlink_to(outside)

      with self.assertRaises(ValueError):
        store.publish(unsafe)

      self.assertEqual(store.ready_pointer()["generation"], pointer["generation"])
      store.discard_staging(unsafe)

  def test_next_stage_rejects_symlink_added_to_published_generation(self):
    with tempfile.TemporaryDirectory() as raw:
      store = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      _, staging = store.start_staging(seed)
      (staging / "graph.json").write_text('{"nodes":[]}', encoding="utf-8")
      pointer = store.publish(staging)
      outside = Path(raw) / "owner-secret.txt"
      outside.write_text("must not be copied", encoding="utf-8")
      generation = store.generation_path(pointer["generation"])
      (generation / "notes" / "escape.md").symlink_to(outside)

      with self.assertRaises(ValueError):
        store.start_staging(seed)

      self.assertFalse(any(store.GENERATIONS.glob(".staging-*")))

  def test_stage_does_not_follow_symlink_raced_in_after_validation(self):
    with tempfile.TemporaryDirectory() as raw:
      store = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      _, staging = store.start_staging(seed)
      (staging / "graph.json").write_text('{"nodes":[]}', encoding="utf-8")
      pointer = store.publish(staging)
      generation = store.generation_path(pointer["generation"])
      outside = Path(raw) / "owner-secret.txt"
      outside.write_text("must never be dereferenced", encoding="utf-8")
      original_reject = store._reject_unsafe_entries
      calls = 0

      def race_after_validation(root):
        nonlocal calls
        calls += 1
        original_reject(root)
        if calls == 1:
          (generation / "notes" / "raced.md").symlink_to(outside)

      with mock.patch.object(store, "_reject_unsafe_entries", race_after_validation):
        with self.assertRaises(ValueError):
          store.start_staging(seed)

      self.assertFalse(any(store.GENERATIONS.glob(".staging-*")))
      self.assertEqual(outside.read_text(), "must never be dereferenced")

  def test_published_generations_are_not_pruned_while_readers_can_pin_them(self):
    with tempfile.TemporaryDirectory() as raw:
      store = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      names = []
      for i in range(7):
        _, staging = store.start_staging(seed)
        (staging / "graph.json").write_text(json.dumps({"run": i}), encoding="utf-8")
        names.append(store.publish(staging)["generation"])

      for i, generation in enumerate(names):
        self.assertEqual(
          json.loads(store.read_generation_file(generation, "graph.json")),
          {"run": i},
        )


if __name__ == "__main__":
  unittest.main()
