import importlib
import fcntl
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


def _publish(store, seed: Path, value: int = 0):
  _, worktree = store.start_staging(seed)
  (worktree / "graph.json").write_text(
    json.dumps({"run": value, "nodes": [], "edges": [], "problems": []}),
    encoding="utf-8",
  )
  return store.publish(worktree)


class MemoryStoreTests(unittest.TestCase):
  def test_failed_or_discarded_worktree_leaves_pinned_commit_readable(self):
    with tempfile.TemporaryDirectory() as raw:
      store = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      pointer = _publish(store, seed)
      _, worktree = store.start_staging(seed)
      (worktree / "graph.json").write_text('{"unpublished":true}', encoding="utf-8")
      (worktree / "notes" / "partial.md").write_text("partial", encoding="utf-8")

      self.assertNotIn(
        "unpublished", store.read_revision_file(pointer["commit"], "graph.json"),
      )
      store.discard_staging(worktree)

      self.assertEqual(store.ready_pointer()["commit"], pointer["commit"])
      self.assertEqual(
        json.loads(store.read_revision_file(pointer["commit"], "graph.json"))["run"],
        0,
      )
      self.assertFalse((store.REPOSITORY / "notes" / "partial.md").exists())

  def test_commit_is_complete_before_pointer_advances(self):
    with tempfile.TemporaryDirectory() as raw:
      store = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      _, worktree = store.start_staging(seed)
      (worktree / "notes" / "fact.md").write_text("durable fact", encoding="utf-8")
      (worktree / "graph.json").write_text(
        '{"nodes":[],"edges":[],"problems":[]}', encoding="utf-8",
      )

      pointer = store.publish(worktree)

      self.assertEqual(json.loads(store.READY.read_text()), pointer)
      self.assertEqual(pointer["schema"], 2)
      self.assertEqual(pointer["repository"], "repository")
      self.assertEqual(
        store.read_revision_file(pointer["commit"], "notes/fact.md"),
        "durable fact",
      )

  def test_publish_rejects_symlink_without_advancing_pointer(self):
    with tempfile.TemporaryDirectory() as raw:
      store = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      pointer = _publish(store, seed)
      _, worktree = store.start_staging(seed)
      outside = Path(raw) / "outside"
      outside.write_text("secret", encoding="utf-8")
      (worktree / "notes" / "escape.md").symlink_to(outside)

      with self.assertRaises(ValueError):
        store.publish(worktree)

      self.assertEqual(store.ready_pointer()["commit"], pointer["commit"])
      store.discard_staging(worktree)
      self.assertEqual(outside.read_text(), "secret")

  def test_unchanged_run_does_not_create_a_commit(self):
    with tempfile.TemporaryDirectory() as raw:
      store = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      first = _publish(store, seed)
      _, worktree = store.start_staging(seed)

      second = store.publish(worktree)

      self.assertFalse(second["changed"])
      self.assertEqual(second["commit"], first["commit"])
      self.assertEqual(store._git("rev-list", "--count", "main", text=True).stdout.strip(), "1")

  def test_all_published_commits_remain_readable_without_tree_copies(self):
    with tempfile.TemporaryDirectory() as raw:
      store = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      commits = [_publish(store, seed, i)["commit"] for i in range(7)]

      for i, commit in enumerate(commits):
        self.assertEqual(json.loads(store.read_revision_file(commit, "graph.json"))["run"], i)
      self.assertFalse(store.LEGACY_GENERATIONS.exists())
      self.assertFalse(any(path.name.startswith(".staging-") for path in store.ROOT.iterdir()))

  def test_all_schema_one_generations_move_into_git_and_recovery_copies_are_retained(self):
    with tempfile.TemporaryDirectory() as raw:
      store = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      old_generation = "20260712T120000Z-bbbbbbbbbbbb"
      generation = "20260713T120000Z-aaaaaaaaaaaa"
      old_legacy = store.LEGACY_GENERATIONS / old_generation
      current_legacy = store.LEGACY_GENERATIONS / generation
      _seed(old_legacy)
      _seed(current_legacy)
      (old_legacy / "graph.json").write_text(
        '{"run":0,"nodes":[],"edges":[],"problems":[]}', encoding="utf-8",
      )
      (current_legacy / "notes" / "old.md").write_text(
        "legacy fact", encoding="utf-8",
      )
      (current_legacy / "graph.json").write_text(
        '{"run":1,"nodes":[],"edges":[],"problems":[]}', encoding="utf-8",
      )
      store.ROOT.mkdir(parents=True, exist_ok=True)
      store._atomic_text(
        store.READY, json.dumps({"schema": 1, "generation": generation}),
      )

      _, worktree = store.start_staging(seed)
      first = store.publish(worktree)
      imported = store._git("rev-list", "--reverse", "main", text=True).stdout.splitlines()
      second = _publish(store, seed, 2)

      self.assertEqual(len(imported), 2)
      self.assertEqual(
        json.loads(store.read_revision_file(imported[0], "graph.json"))["run"], 0,
      )
      self.assertEqual(
        store.read_revision_file(first["commit"], "notes/old.md"), "legacy fact",
      )
      self.assertNotEqual(first["commit"], second["commit"])
      self.assertTrue(store.LEGACY_GENERATIONS.exists())
      self.assertEqual(first["legacy_generations_imported"], 2)
      self.assertTrue(first["legacy_generations_retained"])
      rolled = store.rollback(imported[0])
      self.assertTrue(rolled["legacy_generations_retained"])
      self.assertTrue(store.LEGACY_GENERATIONS.exists())

  def test_rollback_creates_a_new_commit_with_the_old_tree(self):
    with tempfile.TemporaryDirectory() as raw:
      store = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      first = _publish(store, seed, 1)
      second = _publish(store, seed, 2)

      rolled = store.rollback(first["commit"])

      self.assertNotEqual(rolled["commit"], first["commit"])
      self.assertNotEqual(rolled["commit"], second["commit"])
      self.assertEqual(rolled["rollback_of"], first["commit"])
      self.assertEqual(
        json.loads(store.read_revision_file(rolled["commit"], "graph.json"))["run"], 1,
      )
      self.assertEqual(store._git("rev-list", "--count", "main", text=True).stdout.strip(), "3")

  def test_rollback_refuses_to_race_scheduled_maintenance(self):
    with tempfile.TemporaryDirectory() as raw:
      store = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      pointer = _publish(store, seed, 1)
      store.OPERATION_LOCK.parent.mkdir(parents=True, exist_ok=True)

      with store.OPERATION_LOCK.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaisesRegex(RuntimeError, "currently running"):
          store.rollback(pointer["commit"])

  def test_interrupted_legacy_pointer_swap_finishes_on_retry(self):
    with tempfile.TemporaryDirectory() as raw:
      store = _load(Path(raw))
      seed = Path(raw) / "seed"
      _seed(seed)
      generation = "20260713T120000Z-aaaaaaaaaaaa"
      legacy = store.LEGACY_GENERATIONS / generation
      _seed(legacy)
      (legacy / "graph.json").write_text(
        '{"nodes":[],"edges":[],"problems":[]}', encoding="utf-8",
      )
      store.ROOT.mkdir(parents=True, exist_ok=True)
      store._atomic_text(
        store.READY, json.dumps({"schema": 1, "generation": generation}),
      )

      with mock.patch.object(store, "_atomic_text", side_effect=OSError("crash")):
        with self.assertRaises(OSError):
          store.start_staging(seed)

      _, worktree = store.start_staging(seed)
      pointer = store.publish(worktree)

      self.assertEqual(pointer["schema"], 2)
      self.assertEqual(pointer["legacy_generations_imported"], 1)
      self.assertTrue(store.LEGACY_GENERATIONS.exists())
      self.assertTrue(pointer["legacy_generations_retained"])


if __name__ == "__main__":
  unittest.main()
