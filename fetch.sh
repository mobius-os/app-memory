#!/bin/bash
# fetch.sh — scheduled Memory maintenance wrapper.
#
# The Memory app UI remains a read-only graph browser. This cron job is the
# privileged maintenance path: it runs the Memory agent under a lock, lets it
# consolidate /data/shared/memory, records a cron_outcome, and leaves
# /data/shared/memory/update-log/*.jsonl for Reflection to review.
set -uo pipefail

APP_ID="${1:-}"
API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATA_DIR="${DATA_DIR:-/data}"
LOG="$DATA_DIR/cron-logs/memory.log"
LOCK="$DATA_DIR/cron-logs/memory.lock"
HEARTBEAT="$DATA_DIR/cron-logs/memory.heartbeat"
TOKEN_FILE="$DATA_DIR/service-token.txt"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
RUNNER="${MEMORY_RUNNER:-$SCRIPT_DIR/memory_runner.py}"
RUN_TIMEOUT="${MEMORY_TIMEOUT:-3600}"

export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$DATA_DIR/cli-auth/claude}"
export CODEX_HOME="${CODEX_HOME:-$DATA_DIR/cli-auth/codex}"
export API_BASE_URL DATA_DIR

mkdir -p "$DATA_DIR/cron-logs"
log() { echo "[$(date -Iseconds)] memory: $*" >>"$LOG"; }

emit_outcome() {
  local exit_code="$1"
  [[ -r "$TOKEN_FILE" ]] || return 0
  local token ts payload
  token="$(cat "$TOKEN_FILE")"
  ts="$(date -u +"%Y-%m-%dT%H:%M:%S+00:00")"
  payload="$(printf '{"ev":"cron_outcome","ts":"%s","app_id":%s,"job":"memory","exit_code":%s}' \
    "$ts" "${APP_ID:-0}" "$exit_code")"
  local attempt=0
  while (( attempt < 3 )); do
    if curl -fsS -X POST \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "$API_BASE_URL/api/admin/activity/emit" >/dev/null 2>>"$LOG"; then
      return 0
    fi
    attempt=$(( attempt + 1 ))
    (( attempt < 3 )) && { log "WARN cron_outcome emit attempt $attempt failed; retrying"; sleep $(( 2 ** attempt )); }
  done
  log "WARN cron_outcome emit failed after 3 attempts (rc=$exit_code)"
  return 1
}

exec 9>"$LOCK"
if ! flock -n 9; then
  log "another memory run holds the lock; skipping (exit 5)"
  emit_outcome 5
  exit 5
fi

if [[ ! -r "$TOKEN_FILE" ]]; then
  log "ERROR service token unreadable ($TOKEN_FILE)"
  emit_outcome 3
  exit 3
fi
SERVICE_TOKEN="$(cat "$TOKEN_FILE")"
export SERVICE_TOKEN AGENT_TOKEN="$SERVICE_TOKEN"

if [[ -z "$APP_ID" ]]; then
  log "ERROR no app id passed as \$1"
  emit_outcome 2
  exit 2
fi

if [[ "${MEMORY_DRY:-0}" == "1" ]]; then
  log "dry run requested; skipping Memory agent"
  emit_outcome 0
  exit 0
fi

if [[ ! -f "$RUNNER" ]]; then
  log "ERROR runner not found: $RUNNER"
  emit_outcome 4
  exit 4
fi

log "start (app_id=$APP_ID timeout=${RUN_TIMEOUT}s)"
(
  while true; do
    date -Iseconds >"$HEARTBEAT"
    sleep 60
  done
) &
HB_PID=$!
trap 'kill "$HB_PID" 2>/dev/null || true' EXIT

timeout --signal=TERM --kill-after=60 "$RUN_TIMEOUT" \
  python3 "$RUNNER" >>"$LOG" 2>&1
RC=$?

kill "$HB_PID" 2>/dev/null || true
wait "$HB_PID" 2>/dev/null || true
log "finish rc=$RC"
emit_outcome "$RC"
exit "$RC"
