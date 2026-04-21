#!/usr/bin/env bash
# Polymarket Phase 1 pipeline — cron wrapper.
#
# Cron: */5 * * * * /opt/ambmh/scripts/polymarket_pipeline.sh
#
# Features:
#   - flock-based lockfile prevents overlapping runs if pipeline slows
#   - writes polymarket_pipeline_status.json for monitoring
#   - on failure, sends Telegram alert (rate-limited to 1/hour)
#   - supports docker (default) or bare python mode via USE_DOCKER env var
#
# Env overrides:
#   USE_DOCKER=1|0                 run inside docker (default 1)
#   DOCKER_SERVICE=telegram-bot    compose service with polymarket mounted
#   POLY_MARKETS_LIMIT=20          markets per run
#   POLY_WALLETS_CAP=30            max wallets recomputed per run
#   POLY_EXTRA_ARGS=""             passthrough extra CLI args
#   PROJECT_ROOT=/opt/ambmh
#   LOG_DIR=/var/log/ambmh
#   LOCK_FILE=/tmp/polymarket_pipeline.lock

set -u  # treat unset vars as errors; but do NOT use set -e so we can handle failures
set -o pipefail

# ─── Paths & defaults ───────────────────────────────────────────────────────
PROJECT_ROOT="${PROJECT_ROOT:-/opt/ambmh}"
LOG_DIR="${LOG_DIR:-/var/log/ambmh}"
LOCK_FILE="${LOCK_FILE:-/tmp/polymarket_pipeline.lock}"
LOG_FILE="${LOG_DIR}/polymarket.log"
STATUS_FILE="${PROJECT_ROOT}/data/reports/polymarket_pipeline_status.json"
ALERT_STATE_FILE="/tmp/polymarket_pipeline_alert.state"

USE_DOCKER="${USE_DOCKER:-1}"
DOCKER_SERVICE="${DOCKER_SERVICE:-telegram-bot}"
COMPOSE_FILE="${COMPOSE_FILE:-${PROJECT_ROOT}/docker-compose.prod.yml}"
POLY_MARKETS_LIMIT="${POLY_MARKETS_LIMIT:-20}"
POLY_WALLETS_CAP="${POLY_WALLETS_CAP:-30}"
POLY_EXTRA_ARGS="${POLY_EXTRA_ARGS:-}"

mkdir -p "$LOG_DIR" "$(dirname "$STATUS_FILE")"

# ─── Logging helper ─────────────────────────────────────────────────────────
log() {
    local ts
    ts="$(date -u '+%Y-%m-%d %H:%M:%S')"
    echo "[$ts] $*" >> "$LOG_FILE"
}

# ─── Lockfile (flock on Linux cron host; mkdir fallback elsewhere) ──────────
LOCK_DIR="${LOCK_FILE}.d"
acquire_lock() {
    if command -v flock >/dev/null 2>&1; then
        exec 200>"$LOCK_FILE"
        flock -n 200
        return $?
    fi
    # Portable atomic fallback via mkdir
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        trap 'rm -rf "$LOCK_DIR"' EXIT
        return 0
    fi
    # Stale lock detection (> 30 min)
    if [ -d "$LOCK_DIR" ]; then
        local lock_mtime age now
        now="$(date +%s)"
        lock_mtime="$(stat -c %Y "$LOCK_DIR" 2>/dev/null || stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0)"
        age=$(( now - lock_mtime ))
        if [ "$age" -gt 1800 ]; then
            rm -rf "$LOCK_DIR"
            if mkdir "$LOCK_DIR" 2>/dev/null; then
                trap 'rm -rf "$LOCK_DIR"' EXIT
                return 0
            fi
        fi
    fi
    return 1
}
if ! acquire_lock; then
    log "previous run still holding lock; skipping"
    exit 0
fi

TS_START="$(date -u '+%Y-%m-%d %H:%M:%S')"
TS_START_EPOCH="$(date -u '+%s')"
log "pipeline start"

# ─── Load .env if present ───────────────────────────────────────────────────
cd "$PROJECT_ROOT" 2>/dev/null || {
    log "ERROR: cannot cd to $PROJECT_ROOT"
    exit 2
}
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

# ─── Run pipeline ───────────────────────────────────────────────────────────
EXIT_CODE=0
if [ "$USE_DOCKER" = "1" ]; then
    # shellcheck disable=SC2086
    docker compose -f "$COMPOSE_FILE" exec -T "$DOCKER_SERVICE" \
        python -m polymarket.pipeline \
            --markets-limit "$POLY_MARKETS_LIMIT" \
            --wallets-cap "$POLY_WALLETS_CAP" \
            $POLY_EXTRA_ARGS \
        >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?
else
    # shellcheck disable=SC2086
    python -m polymarket.pipeline \
        --markets-limit "$POLY_MARKETS_LIMIT" \
        --wallets-cap "$POLY_WALLETS_CAP" \
        $POLY_EXTRA_ARGS \
        >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?
fi

TS_END="$(date -u '+%Y-%m-%d %H:%M:%S')"
TS_END_EPOCH="$(date -u '+%s')"
DURATION=$((TS_END_EPOCH - TS_START_EPOCH))

if [ $EXIT_CODE -eq 0 ]; then
    RESULT="ok"
    log "pipeline ok (${DURATION}s)"
else
    RESULT="fail"
    log "pipeline FAIL (exit=$EXIT_CODE, ${DURATION}s)"
fi

# ─── Status file ────────────────────────────────────────────────────────────
# JSON encoded manually to avoid needing jq. Values above are sanitised already.
cat > "$STATUS_FILE" <<EOF
{
  "last_run_start": "${TS_START}",
  "last_run_end": "${TS_END}",
  "duration_seconds": ${DURATION},
  "result": "${RESULT}",
  "exit_code": ${EXIT_CODE},
  "mode": "$([ "$USE_DOCKER" = "1" ] && echo "docker" || echo "bare")",
  "markets_limit": ${POLY_MARKETS_LIMIT},
  "wallets_cap": ${POLY_WALLETS_CAP}
}
EOF

# ─── Telegram failure alert (rate-limited: 1/hour) ──────────────────────────
maybe_alert_failure() {
    [ "$RESULT" = "fail" ] || return 0
    [ -n "${TELEGRAM_TOKEN:-}" ] || return 0
    [ -n "${TELEGRAM_CHAT_ID:-}" ] || return 0

    local now_epoch last_alert_epoch
    now_epoch="$TS_END_EPOCH"
    last_alert_epoch=0
    [ -f "$ALERT_STATE_FILE" ] && last_alert_epoch="$(cat "$ALERT_STATE_FILE" 2>/dev/null || echo 0)"
    if [ $((now_epoch - last_alert_epoch)) -lt 3600 ]; then
        log "failure alert throttled (last sent < 1h ago)"
        return 0
    fi

    # Take last 15 lines of log as failure context (tail, not cat, to avoid huge msg)
    local context
    context="$(tail -n 15 "$LOG_FILE" | sed 's/"/\\"/g' | tr '\n' ' ' | cut -c1-800)"

    curl -sS -m 10 \
        "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=[POLY] pipeline FAILED at ${TS_END} (exit=${EXIT_CODE}, ${DURATION}s). Tail: ${context}" \
        >> "$LOG_FILE" 2>&1 \
        && echo "$now_epoch" > "$ALERT_STATE_FILE"
}
maybe_alert_failure

exit $EXIT_CODE
