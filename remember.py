"""Save one fact a working agent learned for Memory's nightly writer.

Usage: python3 remember.py "<one self-contained fact>" "$CHAT_ID"
"""

from __future__ import annotations

import json
import sys

from memory_store import append_capture

ACTIVITY_ID = "memory-capture"
RESULT_PREFIX = "MOBIUS_APP_ACTIVITY_V1:"


def _receipt(status: str, label: str, detail: str) -> str:
  return RESULT_PREFIX + json.dumps({
    "activity_id": ACTIVITY_ID,
    "status": status,
    "label": label,
    "detail": detail,
  }, ensure_ascii=False)


def main(argv: list[str]) -> int:
  if len(argv) != 3:
    print(__doc__.strip(), file=sys.stderr)
    return 2
  try:
    record = append_capture(argv[2], argv[1])
  except (OSError, ValueError) as exc:
    print(_receipt("failed", "Could not save to Memory", str(exc)))
    return 1
  print(
    "Saved. Memory's nightly writer will check it against this chat and "
    "file it in the graph."
  )
  print(_receipt("succeeded", "Saved to Memory", record["text"]))
  return 0


if __name__ == "__main__":
  raise SystemExit(main(sys.argv))
