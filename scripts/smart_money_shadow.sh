#!/usr/bin/env bash
# smart_money_shadow.sh — operate the smart-money-shadow daemon.
#
# Usage:
#     scripts/smart_money_shadow.sh {status|logs|restart|stop|start|tail}
#
# Notes:
#   - logs: shows last 200 lines; use `tail` for live follow.
#   - restart: preserves signal history (state machine persists via DB).
#   - stop: graceful; daemon will flush queue and unsubscribe WS.

set -euo pipefail

SERVICE="smart-money-shadow"
PROJECT_ROOT="${PROJECT_ROOT:-/opt/ambmh}"
COMPOSE_FILE="${COMPOSE_FILE:-${PROJECT_ROOT}/docker-compose.prod.yml}"

cd "$PROJECT_ROOT"

if [ $# -lt 1 ]; then
    echo "usage: $0 {status|logs|restart|stop|start|tail|inspect}" >&2
    exit 64
fi

CMD="$1"
shift

case "$CMD" in
    status)
        docker compose -f "$COMPOSE_FILE" ps "$SERVICE"
        ;;
    logs)
        docker compose -f "$COMPOSE_FILE" logs --tail=200 "$SERVICE"
        ;;
    tail)
        docker compose -f "$COMPOSE_FILE" logs -f --tail=100 "$SERVICE"
        ;;
    restart)
        docker compose -f "$COMPOSE_FILE" restart "$SERVICE"
        ;;
    stop)
        docker compose -f "$COMPOSE_FILE" stop "$SERVICE"
        ;;
    start)
        docker compose -f "$COMPOSE_FILE" up -d "$SERVICE"
        ;;
    inspect)
        # Quick stats: how many signals / paper trades / skips in the last hour
        # via direct psql (uses DATABASE_URL from .env).
        docker compose -f "$COMPOSE_FILE" exec -T smart-money-shadow python -c "
import os, sys
os.environ.setdefault('PYTHONPATH', '/app')
try:
    import psycopg
    url = os.environ.get('DATABASE_URL', '')
    if not url:
        print('DATABASE_URL not set', file=sys.stderr); sys.exit(1)
    with psycopg.connect(url) as c, c.cursor() as cur:
        cur.execute(\"select count(*), max(opened_at) from sm_paper_trades where opened_at > now() - interval '1 hour'\")
        n_paper, last_paper = cur.fetchone()
        cur.execute(\"select count(*), max(created_at) from sm_skipped_signals where created_at > now() - interval '1 hour'\")
        n_skip, last_skip = cur.fetchone()
        cur.execute(\"select reason, count(*) from sm_skipped_signals where created_at > now() - interval '1 hour' group by reason order by 2 desc\")
        reasons = cur.fetchall()
        cur.execute(\"select count(*) from sm_wallet_positions where side != 'flat'\")
        n_open, = cur.fetchone()
    print(f'paper trades (1h): {n_paper}, latest {last_paper}')
    print(f'skipped signals (1h): {n_skip}, latest {last_skip}')
    for r, c in reasons:
        print(f'  {r}: {c}')
    print(f'open positions tracked: {n_open}')
except Exception as e:
    print(f'inspect failed: {e}', file=sys.stderr)
    sys.exit(1)
"
        ;;
    *)
        echo "error: unknown command '$CMD'" >&2
        exit 64
        ;;
esac
