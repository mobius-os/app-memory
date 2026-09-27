"""Replay benchmark: score live recall against hindsight-verified expectations.

The nightly recall audit already judges, with the later conversation as
hindsight, which notes each real lookup should have returned. `freeze` turns the
most recent audited lookups into a fixed replay set; `run` replays those exact
questions against the currently published graph through the same live reader
(without read telemetry, so replays never become audit work) and reports recall,
noise, tokens, and time beside the previous run. Use it before and after any
change to search, organisation, admission, or consolidation.

  python3 memory_benchmark.py freeze [size]
  python3 memory_benchmark.py run [parallel] [walk|single-pass|deep]
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from memory_search import (
  RESULT_EMPTY,
  RESULT_FAILED,
  RESULT_HIT,
  RESULT_REASON_NOT_READY,
  RecallResult,
  _prepare_request,
  deep_replay_batch,
  retrieve,
)
from memory_store import STATE, ready_pointer, read_revision_file

BENCHMARK = STATE / "benchmark"
REPLAY_SET = BENCHMARK / "replay-set.json"
RUNS = BENCHMARK / "runs"
DEFAULT_SIZE = 50
DEFAULT_PARALLEL = 4


def retrieve_deep(question: str) -> RecallResult:
  """The nightly reference lookup against the published graph, untracked."""
  request = _prepare_request(question, "")
  if request.commit is None:
    return RecallResult(RESULT_FAILED, "Memory lookup failed.", reason=RESULT_REASON_NOT_READY)
  [result], _attempts = deep_replay_batch([request.question], request.commit)
  files = tuple((result or {}).get("selected") or ())
  return RecallResult(
    RESULT_HIT if files else RESULT_EMPTY, "Deep reference selection.",
    files=files, commit=request.commit,
  )


READERS = {
  "walk": lambda question: retrieve(question, "walk"),
  "single-pass": lambda question: retrieve(question, "single-pass"),
  "deep": retrieve_deep,
}


def _jsonl(pattern: str) -> list[dict]:
  rows = []
  for path in sorted(STATE.glob(pattern)):
    for line in path.read_text(encoding="utf-8").splitlines():
      try:
        row = json.loads(line)
      except ValueError:
        continue
      if isinstance(row, dict):
        rows.append(row)
  return rows


def _paths(value) -> set[str]:
  return {item for item in value or () if isinstance(item, str)}


def _current_note_paths() -> set[str]:
  pointer = ready_pointer()
  if not pointer:
    raise SystemExit("Memory has no published graph yet.")
  graph = json.loads(read_revision_file(pointer["commit"], "graph.json"))
  return {node["path"] for node in graph.get("nodes", []) if node.get("path")}


def freeze(size: int = DEFAULT_SIZE) -> dict:
  """Freeze the most recent audited lookups whose expected notes still exist.

  Expected = what the reader returned minus what the audit called overreach,
  plus what the audit says it missed. Lookups whose hindsight found nothing
  worth recalling carry no expectation to score and are left out.
  """
  questions = {
    row["read_id"]: row["question"] for row in _jsonl("read-log/*.jsonl")
    if isinstance(row.get("read_id"), str) and isinstance(row.get("question"), str)
  }
  existing = _current_note_paths()
  cases: dict[str, dict] = {}
  for audit in _jsonl("recall-audit/*.jsonl"):
    read_id = audit.get("read_id")
    if audit.get("outcome") not in {"ok", "miss"} or read_id not in questions:
      continue
    # Only a judged verdict is an expectation; unreviewed replay readings are
    # the reference reader's opinion, not ground truth.
    if audit.get("verdict_source", "writer") != "writer":
      continue
    expected = (
      _paths(audit.get("live_selected")) - _paths(audit.get("overselected_nodes"))
    ) | _paths(audit.get("missed_nodes"))
    surviving = expected & existing
    # A case whose expectation was mostly merged away no longer tests routing.
    if not surviving or len(surviving) * 2 < len(expected):
      continue
    cases[read_id] = {
      "read_id": read_id,
      "audited_at": audit.get("at"),
      "outcome": audit.get("outcome"),
      "question": questions[read_id],
      "expected": sorted(surviving),
    }
  chosen = sorted(cases.values(), key=lambda case: case["audited_at"] or "")[-size:]
  replay = {
    "schema": 1,
    "frozen_at": datetime.now(UTC).isoformat(),
    "cases": chosen,
  }
  BENCHMARK.mkdir(parents=True, exist_ok=True)
  REPLAY_SET.write_text(json.dumps(replay, indent=1, ensure_ascii=False) + "\n")
  return replay


def _usage(result) -> tuple[int, int, list[str]]:
  tokens = calls = 0
  providers = []
  for decision in (result.traversal.decisions if result.traversal else ()):
    for attempt in decision.get("attempts") or ():
      if attempt.get("skipped"):
        continue
      calls += 1
      if attempt.get("outcome") == "ok":
        providers.append(attempt.get("provider"))
      usage = (attempt.get("usage_receipt") or {}).get("usage") or {}
      tokens += sum(v for v in usage.values() if isinstance(v, int))
  return tokens, calls, providers


def _replay(case: dict, existing: set[str], reader=None) -> dict:
  started = time.monotonic()
  result = (reader or READERS["walk"])(case["question"])
  seconds = round(time.monotonic() - started, 1)
  # Notes merged away since freezing cannot be found by any reader.
  expected = set(case["expected"]) & existing
  selected = set(result.files)
  found = selected & expected
  tokens, calls, providers = _usage(result)
  return {
    "read_id": case["read_id"],
    "status": result.status,
    "expected": len(expected),
    "found": len(found),
    "selected": len(selected),
    "missed": sorted(expected - selected),
    "extra": sorted(selected - expected),
    "recall": len(found) / len(expected) if expected else None,
    "tokens": tokens,
    "model_calls": calls,
    "providers": sorted(set(providers)),
    "seconds": seconds,
  }


def _summary(results: list[dict]) -> dict:
  scored = [r for r in results if r["recall"] is not None]
  selected = sum(r["selected"] for r in results)
  return {
    "cases": len(results),
    "mean_recall": round(statistics.mean(r["recall"] for r in scored), 3),
    "full_hits": sum(1 for r in scored if r["found"] == r["expected"]),
    "noise": round(
      sum(len(r["extra"]) for r in results) / selected, 3,
    ) if selected else 0.0,
    "median_tokens": statistics.median(r["tokens"] for r in results),
    "median_seconds": statistics.median(r["seconds"] for r in results),
    "median_model_calls": statistics.median(r["model_calls"] for r in results),
    "failed": sum(1 for r in results if r["status"] == "failed"),
  }


def run(parallel: int = DEFAULT_PARALLEL, reader: str = "walk") -> dict:
  try:
    replay = json.loads(REPLAY_SET.read_text(encoding="utf-8"))
  except FileNotFoundError:
    raise SystemExit("No replay set yet; run `memory_benchmark.py freeze` first.")
  existing = _current_note_paths()
  previous = sorted(RUNS.glob("*.json"))
  with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
    results = list(pool.map(
      lambda case: _replay(case, existing, READERS[reader]), replay["cases"],
    ))
  record = {
    "schema": 1,
    "at": datetime.now(UTC).isoformat(),
    "reader": reader,
    "graph_commit": ready_pointer()["commit"],
    "replay_frozen_at": replay["frozen_at"],
    "summary": _summary(results),
    "results": results,
  }
  RUNS.mkdir(parents=True, exist_ok=True)
  stamp = record["at"].replace(":", "").replace("-", "")[:15]
  (RUNS / f"{stamp}.json").write_text(
    json.dumps(record, indent=1, ensure_ascii=False) + "\n",
  )
  if previous:
    before = json.loads(previous[-1].read_text(encoding="utf-8"))
    if before.get("replay_frozen_at") == replay["frozen_at"]:
      record["previous_summary"] = before["summary"]
  return record


def main(argv: list[str]) -> int:
  command = argv[1] if len(argv) > 1 else ""
  if command == "freeze":
    replay = freeze(int(argv[2]) if len(argv) > 2 else DEFAULT_SIZE)
    print(f"Froze {len(replay['cases'])} audited lookups into {REPLAY_SET}.")
    return 0
  if command == "run":
    record = run(
      int(argv[2]) if len(argv) > 2 else DEFAULT_PARALLEL,
      argv[3] if len(argv) > 3 else "walk",
    )
    print(json.dumps({
      key: record[key]
      for key in ("reader", "graph_commit", "summary", "previous_summary")
      if key in record
    }, indent=1))
    return 0
  print(__doc__.strip(), file=sys.stderr)
  return 2


if __name__ == "__main__":
  raise SystemExit(main(sys.argv))
