"""Immutable graph generations, confined reads, and app-owned telemetry."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
ROOT = DATA_DIR / "shared" / "memory"
GENERATIONS = ROOT / "generations"
READY = ROOT / ".ready"
STATE = ROOT / "app-state"
_GEN_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
_SAFE_REL = re.compile(r"^(?:index\.md|(?:mocs|notes)/[a-z0-9][a-z0-9._-]*\.md|graph\.json)$")


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


def ready_pointer() -> dict | None:
  try:
    fd = os.open(READY, os.O_RDONLY | os.O_NOFOLLOW)
    try:
      raw = os.read(fd, 16_385)
    finally:
      os.close(fd)
    if len(raw) > 16_384:
      return None
    value = json.loads(raw.decode("utf-8"))
  except (OSError, ValueError):
    return None
  if not isinstance(value, dict) or value.get("schema") != 1:
    return None
  generation = value.get("generation")
  if not isinstance(generation, str) or not _GEN_RE.fullmatch(generation):
    return None
  path = GENERATIONS / generation
  try:
    if path.is_symlink() or not path.is_dir() or path.resolve().parent != GENERATIONS.resolve():
      return None
  except (OSError, RuntimeError):
    return None
  return value


def generation_path(generation: str) -> Path:
  if not _GEN_RE.fullmatch(generation):
    raise ValueError("invalid generation")
  return GENERATIONS / generation


def start_staging(seed_dir: Path) -> tuple[str, Path]:
  """Create a same-filesystem staging tree from current gen, legacy, or seeds."""
  GENERATIONS.mkdir(parents=True, exist_ok=True)
  staging = GENERATIONS / f".staging-{uuid.uuid4().hex}"
  pointer = ready_pointer()
  if pointer:
    current = generation_path(pointer["generation"])
    # ``copytree(symlinks=False)`` follows links. Validate the source before
    # copying so a post-publication mutation cannot turn a staged generation
    # into a copy of some other mounted file and then erase the evidence.
    _reject_unsafe_entries(current)
  else:
    legacy_sources = [ROOT / "index.md", ROOT / "mocs", ROOT / "notes"]
    for source in legacy_sources:
      if source.is_dir() and not source.is_symlink():
        _reject_unsafe_entries(source)
    if not any(path.exists() for path in legacy_sources):
      if seed_dir.is_symlink() or not seed_dir.is_dir():
        raise ValueError("unsafe or missing seed tree")
      _reject_unsafe_entries(seed_dir)
  staging.mkdir(mode=0o770)
  try:
    if pointer:
      shutil.copytree(
        current, staging, dirs_exist_ok=True,
        # Never dereference a link raced in after the validation above. It is
        # copied as a link and the staging-tree validation below rejects the
        # whole run before any publication.
        symlinks=True,
      )
    elif any(path.exists() for path in legacy_sources):
      for source in legacy_sources:
        if source.is_dir() and not source.is_symlink():
          shutil.copytree(
            source, staging / source.name,
            dirs_exist_ok=True, symlinks=True,
          )
        elif source.is_file() and not source.is_symlink():
          _copy_regular_file(source, staging / source.name)
    else:
      shutil.copytree(seed_dir, staging, dirs_exist_ok=True, symlinks=True)
    (staging / "mocs").mkdir(exist_ok=True)
    (staging / "notes").mkdir(exist_ok=True)
    _reject_unsafe_entries(staging)
  except BaseException:
    shutil.rmtree(staging, ignore_errors=True)
    raise
  return uuid.uuid4().hex, staging


def _copy_regular_file(source: Path, target: Path) -> None:
  """Copy one legacy file through an O_NOFOLLOW descriptor."""
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


def _reject_unsafe_entries(root: Path) -> None:
  for path in root.rglob("*"):
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode):
      raise ValueError(f"symlink in memory tree: {path}")
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
      raise ValueError(f"non-file entry in memory tree: {path}")


def _fsync_tree(root: Path) -> None:
  _reject_unsafe_entries(root)
  for path in sorted(root.rglob("*"), reverse=True):
    if path.is_file():
      fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
      try:
        os.fsync(fd)
      finally:
        os.close(fd)
  for path in [*sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True), root]:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
      os.fsync(fd)
    finally:
      os.close(fd)


def publish(staging: Path) -> dict:
  """Rename an immutable generation, then atomically advance the pointer."""
  if staging.parent != GENERATIONS or not staging.name.startswith(".staging-"):
    raise ValueError("staging path is outside generations")
  generation = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
  final = generation_path(generation)
  _fsync_tree(staging)
  os.replace(staging, final)
  dir_fd = os.open(GENERATIONS, os.O_RDONLY | os.O_DIRECTORY)
  try:
    os.fsync(dir_fd)
  finally:
    os.close(dir_fd)
  pointer = {
    "schema": 1,
    "generation": generation,
    "published_at": datetime.now(UTC).isoformat(),
  }
  _atomic_text(READY, json.dumps(pointer, sort_keys=True) + "\n")
  # Generations are immutable and readers pin one by name. Do not prune here:
  # deleting an older directory could invalidate a concurrent pinned read.
  # A future collector must use explicit reader leases before reclaiming them.
  return pointer


def discard_staging(staging: Path | None) -> None:
  if staging and staging.parent == GENERATIONS and staging.name.startswith(".staging-"):
    shutil.rmtree(staging, ignore_errors=True)


def read_generation_file(generation: str, rel: str, *, max_bytes: int = 256_000) -> str:
  """Read one regular file through pinned directory fds without symlink races."""
  if not _SAFE_REL.fullmatch(rel):
    raise ValueError("unsupported memory path")
  generation_path(generation)  # validates the name before using it with openat
  opened: list[int] = []
  try:
    current_fd = os.open(GENERATIONS, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    opened.append(current_fd)
    current_fd = os.open(
      generation, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
      dir_fd=current_fd,
    )
    opened.append(current_fd)
    parts = Path(rel).parts
    for part in parts[:-1]:
      current_fd = os.open(
        part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=current_fd,
      )
      opened.append(current_fd)
    fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=current_fd)
    opened.append(fd)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
      raise ValueError("memory source is not a regular file")
    raw = os.read(fd, max_bytes + 1)
  except OSError as exc:
    raise ValueError("unsafe or missing memory source") from exc
  finally:
    for fd in reversed(opened):
      try:
        os.close(fd)
      except OSError:
        pass
  if len(raw) > max_bytes:
    raise ValueError("memory source exceeds read cap")
  return raw.decode("utf-8")


@contextmanager
def _state_lock():
  STATE.mkdir(parents=True, exist_ok=True)
  path = STATE / ".telemetry.lock"
  with path.open("a+") as handle:
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    yield


def record_read(generation: str, question: str, files: list[str], chat_id: str = "") -> None:
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
      "schema": 1,
      "at": datetime.now(UTC).isoformat(),
      "generation": generation,
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
