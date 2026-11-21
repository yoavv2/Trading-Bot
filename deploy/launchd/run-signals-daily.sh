#!/bin/sh
# Runs the signals-daily Job once for one book (docs/SIGNALS-PLAN.md, Step 2).
#
# launchd starts this after the U.S. close. It submits the Job through the research API
# (the only way a Job is created), lets the worker execute it, and logs what happened.
# Every line starts with the UTC time, so a run that happened late (a sleeping machine,
# late data) can be seen here; the Job's own result records `started_at` and
# `decided_at`, and a signal decided after the next open is recorded `held_back_late`.
#
# Running it twice is safe: the Job writes nothing for a session it already processed.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
BOOK=${SIGNALS_BOOK:-forward-log-2026-11}
DATABASE=${SIGNALS_DATABASE:-trading_research}
PORT=${SIGNALS_API_PORT:-8017}
BASE="http://127.0.0.1:$PORT"

export TRADING_PLATFORM_RESEARCH__MODE=true
export TRADING_PLATFORM_RESEARCH__CALENDAR_START=2014-01-02
export TRADING_PLATFORM_DATABASE__NAME="$DATABASE"
export PYTHONPATH="$ROOT/src"
cd "$ROOT"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

log "start book=$BOOK database=$DATABASE"
TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED=true \
    .venv/bin/uvicorn trading_platform.api.app:app --host 127.0.0.1 --port "$PORT" --log-level warning &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true' EXIT

tries=0
until curl -fsS "$BASE/health" >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ "$tries" -ge 60 ]; then
        log "failed: the API did not start"
        exit 1
    fi
    sleep 1
done

# One Job an hour at most: a second start within the hour replays the first submission.
KEY="signals-daily-$BOOK-$(date -u +%Y%m%dT%H)"
RESPONSE=$(curl -sS -X POST "$BASE/api/v1/jobs" \
    -H "Content-Type: application/json" -H "Idempotency-Key: $KEY" \
    -d "{\"job_type\": \"signals-daily\", \"payload\": {\"book\": \"$BOOK\"}}")
log "submitted key=$KEY response=$RESPONSE"
JOB_ID=$(printf '%s' "$RESPONSE" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin).get("job_id", ""))')
if [ -z "$JOB_ID" ]; then
    log "failed: the Job was not accepted"
    exit 1
fi

.venv/bin/python -m trading_platform.worker run-jobs --once --compact >/dev/null
RESULT=$(curl -sS "$BASE/api/v1/jobs/$JOB_ID")
log "finished job=$JOB_ID result=$RESULT"
printf '%s' "$RESULT" | .venv/bin/python -c '
import json, sys
job = json.load(sys.stdin)
sys.exit(0 if job.get("status") == "succeeded" else 1)'
