"""Commit-addressed Memory graph storage and app-owned telemetry."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
ROOT = DATA_DIR / "shared" / "memory"
REPOSITORY = ROOT / "repository"
LEGACY_GENERATIONS = ROOT / "generations"
READY = ROOT / ".ready"
STATE = ROOT / "app-state"
OPERATION_LOCK = ROOT / ".operation.lock"
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_LEGACY_GEN_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
_SAFE_REL = re.compile(
  r"^(?:index\.md|(?:mocs|notes)/[a-z0-9][a-z0-9._-]*\.md|graph\.json)$"
)
_TRACKED_PATHS = ("index.md", "graph.json", "mocs", "notes")


def _atomic_text(path: Path, text: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
      handle.write(text)
      handle.flush()
      os.fsync(handle.fileno())
    os.replace(tmp, path)
    dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
      os.fsync(dir_fd)
    finally:
      os.close(dir_fd)
  except BaseException:
    try:
      os.unlink(tmp)
    except OSError:
      pass
    raise


def _git_env() -> dict[str, str]:
  return {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "HOME": "/nonexistent",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_AUTHOR_NAME": "Möbius Memory",
    "GIT_AUTHOR_EMAIL": "memory@mobius.local",
    "GIT_COMMITTER_NAME": "Möbius Memory",
    "GIT_COMMITTER_EMAIL": "memory@mobius.local",
  }


def _git_at(
  repo: Path,
  *args: str,
  check: bool = True,
  text: bool = False,
) -> subprocess.CompletedProcess:
  proc = subprocess.run(
    ["git", "--no-pager", "-C", str(repo), *args],
    env=_git_env(), capture_output=True, text=text, timeout=30,
  )
  if check and proc.returncode != 0:
    stderr = proc.stderr if text else proc.stderr.decode("utf-8", "replace")
    raise RuntimeError(f"git {' '.join(args)} failed: {stderr[-1000:]}")
  return proc


def _git(*args: str, check: bool = True, text: bool = False) -> subprocess.CompletedProcess:
  return _git_at(REPOSITORY, *args, check=check, text=text)


def _head() -> str | None:
  if not (REPOSITORY / ".git").is_dir():
    return None
  proc = _git("rev-parse", "--verify", "HEAD", check=False, text=True)
  value = proc.stdout.strip() if proc.returncode == 0 else ""
  return value if _COMMIT_RE.fullmatch(value) else None


def _reachable_commit(commit: str) -> bool:
  if not _COMMIT_RE.fullmatch(commit) or not (REPOSITORY / ".git").is_dir():
    return False
  return _git(
    "merge-base", "--is-ancestor", commit, "refs/heads/main",
    check=False,
  ).returncode == 0


def _read_pointer_bytes() -> dict | None:
  try:
    fd = os.open(READY, os.O_RDONLY | os.O_NOFOLLOW)
    try:
      raw = os.read(fd, 16_385)
    finally:
      os.close(fd)
    if len(raw) > 16_384:
      return None
    value = json.loads(raw.decode("utf-8"))
  except (OSError, ValueError, UnicodeError):
    return None
  return value if isinstance(value, dict) else None


def ready_pointer() -> dict | None:
  value = _read_pointer_bytes()
  if value is None or value.get("schema") != 2:
    return None
  commit = value.get("commit")
  if not isinstance(commit, str) or not _reachable_commit(commit):
    return None
  return value


def _legacy_pointer() -> dict | None:
  value = _read_pointer_bytes()
  if value is None or value.get("schema") != 1:
    return None
  generation = value.get("generation")
  if not isinstance(generation, str) or not _LEGACY_GEN_RE.fullmatch(generation):
    return None
  source = LEGACY_GENERATIONS / generation
  try:
    if source.is_symlink() or not source.is_dir():
      return None
  except OSError:
    return None
  return value


def _copy_regular_file(source: Path, target: Path) -> None:
  fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
  try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
      raise ValueError(f"non-file legacy memory entry: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(fd, "rb", closefd=False) as reader, target.open("xb") as writer:
      shutil.copyfileobj(reader, writer)
      writer.flush()
      os.fsync(writer.fileno())
  finally:
    os.close(fd)


def _reject_source_entries(root: Path) -> None:
  for path in root.rglob("*"):
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode):
      raise ValueError(f"symlink in memory tree: {path}")
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
      raise ValueError(f"non-file entry in memory tree: {path}")


def _copy_source_tree(source: Path, target: Path) -> None:
  _reject_source_entries(source)
  for name in _TRACKED_PATHS:
    item = source / name
    if item.is_dir() and not item.is_symlink():
      shutil.copytree(item, target / name, dirs_exist_ok=True, symlinks=True)
    elif item.is_file() and not item.is_symlink():
      _copy_regular_file(item, target / name)


def _migration_source(seed_dir: Path) -> Path:
  legacy = _legacy_pointer()
  if legacy:
    return LEGACY_GENERATIONS / legacy["generation"]
  if any((ROOT / name).exists() for name in ("index.md", "mocs", "notes")):
    return ROOT
  if seed_dir.is_symlink() or not seed_dir.is_dir():
    raise ValueError("unsafe or missing seed tree")
  return seed_dir


def _legacy_sources(current: str) -> list[Path]:
  """Return every safe legacy snapshot, with the published one last."""
  sources: list[Path] = []
  for path in sorted(LEGACY_GENERATIONS.iterdir()):
    if (
      not _LEGACY_GEN_RE.fullmatch(path.name)
      or path.is_symlink()
      or not path.is_dir()
    ):
      raise ValueError(f"unsafe legacy generation: {path}")
    sources.append(path)
  current_path = LEGACY_GENERATIONS / current
  if current_path not in sources:
    raise ValueError("published legacy generation is missing")
  return [path for path in sources if path != current_path] + [current_path]


def _clear_paths(root: Path) -> None:
  for name in _TRACKED_PATHS:
    path = root / name
    try:
      mode = path.lstat().st_mode
    except FileNotFoundError:
      continue
    if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
      shutil.rmtree(path)
    else:
      path.unlink()


def _commit_at(repo: Path, message: str) -> str:
  _git_at(repo, "add", "-A", "--", *_TRACKED_PATHS)
  _git_at(
    repo,
    "-c", "commit.gpgSign=false", "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsync=committed", "-c", "core.fsyncMethod=fsync",
    "commit", "--allow-empty", "--no-gpg-sign", "--no-verify", "-m", message,
  )
  commit = _git_at(repo, "rev-parse", "--verify", "HEAD", text=True).stdout.strip()
  if not _COMMIT_RE.fullmatch(commit):
    raise RuntimeError("Memory import did not produce a commit")
  return commit


def _ensure_repository(seed_dir: Path) -> None:
  ROOT.mkdir(parents=True, exist_ok=True)
  if REPOSITORY.exists():
    if REPOSITORY.is_symlink() or not (REPOSITORY / ".git").is_dir():
      raise ValueError("unsafe Memory repository")
    legacy = _legacy_pointer()
    head = _head()
    if legacy and head:
      subject = _git("show", "-s", "--format=%s", head, text=True).stdout.strip()
      expected = f"Import legacy memory generation {legacy['generation']}"
      if subject != expected:
        raise ValueError("interrupted legacy migration has an unexpected Git head")
      imported = int(_git("rev-list", "--count", "main", text=True).stdout.strip())
      _atomic_text(READY, json.dumps({
        "schema": 2,
        "repository": "repository",
        "commit": head,
        "published_at": datetime.now(UTC).isoformat(),
        "changed": False,
        "legacy_generations_imported": imported,
      }, sort_keys=True) + "\n")
    pointer = ready_pointer()
    if (
      pointer
      and isinstance(pointer.get("legacy_generations_imported"), int)
      and LEGACY_GENERATIONS.exists()
    ):
      try:
        shutil.rmtree(LEGACY_GENERATIONS)
      except OSError:
        pass
    return
  staging = ROOT / f".repository-init-{uuid.uuid4().hex}"
  staging.mkdir(mode=0o770)
  try:
    subprocess.run(
      ["git", "init", "-b", "main", str(staging)], env=_git_env(),
      capture_output=True, check=True, timeout=30,
    )
    legacy = _legacy_pointer()
    migrated_commit = None
    imported = 0
    if legacy:
      for source in _legacy_sources(legacy["generation"]):
        _clear_paths(staging)
        _copy_source_tree(source, staging)
        (staging / "mocs").mkdir(exist_ok=True)
        (staging / "notes").mkdir(exist_ok=True)
        _validate_tree(staging, require_graph=True)
        migrated_commit = _commit_at(
          staging, f"Import legacy memory generation {source.name}",
        )
        imported += 1
    else:
      _copy_source_tree(_migration_source(seed_dir), staging)
      (staging / "mocs").mkdir(exist_ok=True)
      (staging / "notes").mkdir(exist_ok=True)
      _validate_tree(staging)
    os.replace(staging, REPOSITORY)
    root_fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY)
    try:
      os.fsync(root_fd)
    finally:
      os.close(root_fd)
    if migrated_commit:
      pointer = {
        "schema": 2,
        "repository": "repository",
        "commit": migrated_commit,
        "published_at": datetime.now(UTC).isoformat(),
        "changed": False,
        "legacy_generations_imported": imported,
      }
      _atomic_text(READY, json.dumps(pointer, sort_keys=True) + "\n")
      try:
        shutil.rmtree(LEGACY_GENERATIONS)
      except OSError:
        pass
  except BaseException:
    shutil.rmtree(staging, ignore_errors=True)
    raise


def _reset_to_published() -> None:
  pointer = ready_pointer()
  target = pointer["commit"] if pointer else _head()
  if target:
    _git("reset", "--hard", target)
    _git("clean", "-fd", "--", *_TRACKED_PATHS)


def _clear_worktree() -> None:
  _clear_paths(REPOSITORY)


def start_staging(seed_dir: Path) -> tuple[str, Path]:
  """Prepare the one Git working tree from the published commit.

  This retains the old function name so the runner API stays narrow. Unlike
  the retired generation store, it performs no per-run tree copy: readers pin
  committed objects while the analyst edits the unpublished working tree.
  """
  _ensure_repository(seed_dir)
  if _head() is None:
    # A failed first initialization has no commit to reset to. Rebuild that
    # unpublished worktree from the same migration/seed source on every retry.
    _clear_worktree()
    _copy_source_tree(_migration_source(seed_dir), REPOSITORY)
  else:
    _reset_to_published()
  (REPOSITORY / "mocs").mkdir(exist_ok=True)
  (REPOSITORY / "notes").mkdir(exist_ok=True)
  _validate_worktree(REPOSITORY)
  return uuid.uuid4().hex, REPOSITORY


def _validate_tree(root: Path, *, require_graph: bool = False) -> None:
  for child in root.iterdir():
    if child.name == ".git":
      if child.is_symlink() or not child.is_dir():
        raise ValueError("unsafe Git metadata")
      continue
    if child.name not in _TRACKED_PATHS:
      raise ValueError(f"unexpected memory repository entry: {child.name}")
    mode = child.lstat().st_mode
    if stat.S_ISLNK(mode):
      raise ValueError(f"symlink in memory tree: {child}")
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
      raise ValueError(f"non-file entry in memory tree: {child}")
  index = root / "index.md"
  if index.is_symlink() or not index.is_file():
    raise ValueError(f"unsafe memory file: {index}")
  graph = root / "graph.json"
  if graph.exists() and (graph.is_symlink() or not graph.is_file()):
    raise ValueError(f"unsafe memory file: {graph}")
  if require_graph and not graph.is_file():
    raise ValueError(f"missing memory file: {graph}")
  for directory in (root / "mocs", root / "notes"):
    if directory.is_symlink() or not directory.is_dir():
      raise ValueError(f"unsafe memory directory: {directory}")
    for child in directory.iterdir():
      if child.is_symlink() or not child.is_file() or not _SAFE_REL.fullmatch(
        f"{directory.name}/{child.name}"
      ):
        raise ValueError(f"unsafe memory file: {child}")


def _validate_worktree(root: Path, *, require_graph: bool = False) -> None:
  if root != REPOSITORY:
    raise ValueError("memory worktree is not the repository")
  _validate_tree(root, require_graph=require_graph)


def _fsync_worktree(root: Path) -> None:
  _validate_worktree(root, require_graph=True)
  for name in _TRACKED_PATHS:
    path = root / name
    paths = [path] if path.is_file() else sorted(path.rglob("*"), reverse=True)
    for item in paths:
      if item.is_file():
        fd = os.open(item, os.O_RDONLY | os.O_NOFOLLOW)
        try:
          os.fsync(fd)
        finally:
          os.close(fd)


def publish(staging: Path) -> dict:
  """Commit changed graph files, then atomically advance the commit pointer."""
  if staging != REPOSITORY:
    raise ValueError("publication path is not the Memory repository")
  _fsync_worktree(staging)
  _git("add", "-A", "--", *_TRACKED_PATHS)
  changed = _git("diff", "--cached", "--quiet", check=False).returncode != 0
  prior = ready_pointer()
  if not changed:
    if prior is None:
      raise ValueError("initial Memory repository has no files to commit")
    return {**prior, "changed": False}
  message = "Consolidate memory " + datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
  _git(
    "-c", "commit.gpgSign=false", "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsync=committed", "-c", "core.fsyncMethod=fsync",
    "commit", "--no-gpg-sign", "--no-verify", "-m", message,
  )
  commit = _head()
  if commit is None:
    raise RuntimeError("Memory commit did not produce a HEAD")
  pointer = {
    "schema": 2,
    "repository": "repository",
    "commit": commit,
    "published_at": datetime.now(UTC).isoformat(),
    "changed": True,
  }
  if prior and isinstance(prior.get("legacy_generations_imported"), int):
    pointer["legacy_generations_imported"] = prior["legacy_generations_imported"]
  _atomic_text(READY, json.dumps(pointer, sort_keys=True) + "\n")
  return pointer


def discard_staging(staging: Path | None) -> None:
  if staging == REPOSITORY and (REPOSITORY / ".git").is_dir():
    try:
      if _head() is None:
        _clear_worktree()
      else:
        _reset_to_published()
    except Exception:
      # The next supervised run retries the same reset before editing. Never
      # let cleanup hide the original consolidation failure.
      pass


def read_revision_file(commit: str, rel: str, *, max_bytes: int = 256_000) -> str:
  """Read one regular blob from a reachable commit without a checkout."""
  if not _SAFE_REL.fullmatch(rel) or not _reachable_commit(commit):
    raise ValueError("unsupported memory revision or path")
  entry = _git("ls-tree", "-z", "--full-tree", commit, "--", rel)
  if not entry.stdout.endswith(b"\0"):
    raise ValueError("missing memory source")
  try:
    metadata, listed = entry.stdout[:-1].split(b"\t", 1)
    mode, kind, object_sha = metadata.decode("ascii").split(" ")
    listed_path = listed.decode("utf-8")
  except (ValueError, UnicodeError) as exc:
    raise ValueError("invalid memory source") from exc
  if listed_path != rel or mode not in ("100644", "100755") or kind != "blob":
    raise ValueError("unsafe memory source")
  size_proc = _git("cat-file", "-s", object_sha)
  try:
    size = int(size_proc.stdout.strip())
  except ValueError as exc:
    raise ValueError("invalid memory source size") from exc
  if size > max_bytes:
    raise ValueError("memory source exceeds read cap")
  blob = _git("cat-file", "blob", object_sha)
  if len(blob.stdout) != size:
    raise ValueError("short memory source read")
  return blob.stdout.decode("utf-8")


def rollback(target: str) -> dict:
  """Publish a new commit whose tree matches an earlier reachable commit."""
  ROOT.mkdir(parents=True, exist_ok=True)
  with OPERATION_LOCK.open("a+") as handle:
    try:
      fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
      raise RuntimeError("Memory maintenance is currently running") from exc
    return _rollback_locked(target)


def _rollback_locked(target: str) -> dict:
  if not _reachable_commit(target):
    raise ValueError("rollback target is not in Memory history")
  graph = json.loads(read_revision_file(target, "graph.json"))
  if not isinstance(graph, dict) or graph.get("problems"):
    raise ValueError("rollback target has an invalid graph")
  prior = ready_pointer()
  _reset_to_published()
  _git("read-tree", "--reset", "-u", target)
  (REPOSITORY / "mocs").mkdir(exist_ok=True)
  (REPOSITORY / "notes").mkdir(exist_ok=True)
  _validate_worktree(REPOSITORY, require_graph=True)
  if _git("diff", "--cached", "--quiet", check=False).returncode == 0:
    pointer = ready_pointer()
    if pointer and pointer["commit"] == target:
      return {**pointer, "changed": False}
  _git(
    "-c", "commit.gpgSign=false", "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsync=committed", "-c", "core.fsyncMethod=fsync",
    "commit", "--allow-empty", "--no-gpg-sign", "--no-verify", "-m",
    f"Rollback memory to {target[:12]}",
  )
  commit = _head()
  if commit is None:
    raise RuntimeError("Memory rollback did not produce a commit")
  pointer = {
    "schema": 2, "repository": "repository", "commit": commit,
    "published_at": datetime.now(UTC).isoformat(), "changed": True,
    "rollback_of": target,
  }
  if prior and isinstance(prior.get("legacy_generations_imported"), int):
    pointer["legacy_generations_imported"] = prior["legacy_generations_imported"]
  _atomic_text(READY, json.dumps(pointer, sort_keys=True) + "\n")
  return pointer


@contextmanager
def _state_lock():
  STATE.mkdir(parents=True, exist_ok=True)
  path = STATE / ".telemetry.lock"
  with path.open("a+") as handle:
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    yield


def record_read(commit: str, question: str, files: list[str], chat_id: str = "") -> None:
  """Atomically record usage counters and one bounded retrieval trace."""
  clean_ids = [Path(rel).stem for rel in files if rel.startswith(("notes/", "mocs/"))]
  with _state_lock():
    usage_path = STATE / "usage.json"
    try:
      usage = json.loads(usage_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
      usage = {}
    if not isinstance(usage, dict):
      usage = {}
    for node_id in clean_ids:
      usage[node_id] = int(usage.get(node_id, 0) or 0) + 1
    _atomic_text(usage_path, json.dumps(usage, indent=2, sort_keys=True) + "\n")
    trace_id = re.sub(r"[^A-Za-z0-9-]", "", chat_id)[:64] or uuid.uuid4().hex
    trace = {
      "schema": 2,
      "at": datetime.now(UTC).isoformat(),
      "commit": commit,
      "question_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
      "files": files,
    }
    _atomic_text(
      STATE / "read-trace" / f"{trace_id}.json",
      json.dumps(trace, indent=2, sort_keys=True) + "\n",
    )


def load_usage() -> dict[str, int]:
  try:
    value = json.loads((STATE / "usage.json").read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return {}
  return {
    str(key): int(count) for key, count in value.items()
    if isinstance(key, str) and isinstance(count, int)
  } if isinstance(value, dict) else {}


def main() -> int:
  if len(sys.argv) == 3 and sys.argv[1] == "rollback":
    print(json.dumps(rollback(sys.argv[2]), sort_keys=True))
    return 0
  sys.stderr.write("usage: memory_store.py rollback <40-character-commit>\n")
  return 2


if __name__ == "__main__":
  raise SystemExit(main())
