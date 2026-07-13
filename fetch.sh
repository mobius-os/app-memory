#!/bin/bash
# fetch.sh — scheduled Memory maintenance wrapper.
#
# The Memory app UI remains a read-only graph browser. This cron job is the
# scoped maintenance path: the platform wrapper supplies a short-lived app
# token, this script serializes runs, and the Python runner publishes one
# immutable graph generation. It never reads or forwards an owner/service token.
set -uo pipefail

APP_ID="${1:-}"
API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATA_DIR="${DATA_DIR:-/data}"
JOB_STATE="${APP_JOB_STATE_DIR:-$DATA_DIR/apps/${APP_ID:-unknown}/job-state}"
LOG="$JOB_STATE/memory.log"
LOCK="$JOB_STATE/memory.lock"
HEARTBEAT="$JOB_STATE/memory.heartbeat"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
RUNNER="${MEMORY_RUNNER:-$SCRIPT_DIR/memory_runner.py}"
RUN_TIMEOUT="${MEMORY_TIMEOUT:-3600}"

export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$DATA_DIR/cli-auth/claude}"
export CODEX_HOME="${CODEX_HOME:-$DATA_DIR/cli-auth/codex}"
export API_BASE_URL DATA_DIR

mkdir -p "$JOB_STATE"
log() { echo "[$(date -Iseconds)] memory: $*" >>"$LOG"; }

exec 9>"$LOCK"
if ! flock -n 9; then
  log "another memory run holds the lock; skipping (exit 5)"
  exit 5
fi

if [[ -z "${APP_TOKEN:-}" ]]; then
  log "ERROR scoped APP_TOKEN was not supplied by the platform job wrapper"
  exit 3
fi

if [[ -z "$APP_ID" ]]; then
  log "ERROR no app id passed as \$1"
  exit 2
fi
export MEMORY_APP_ID="$APP_ID"

if [[ "${MEMORY_DRY:-0}" == "1" ]]; then
  log "dry run requested; skipping Memory agent"
  exit 0
fi

if [[ ! -f "$RUNNER" ]]; then
  log "ERROR runner not found: $RUNNER"
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
  python3 "$RUNNER" "$APP_ID" >>"$LOG" 2>&1
RC=$?

kill "$HB_PID" 2>/dev/null || true
wait "$HB_PID" 2>/dev/null || true
log "finish rc=$RC"
exit "$RC"
