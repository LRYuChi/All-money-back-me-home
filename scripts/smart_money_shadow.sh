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
        # Signal density snapshot at 1h / 6h / 24h windows.
        # Uses DATABASE_URL from the container's env (never leaks to caller).
        docker compose -f "$COMPOSE_FILE" exec -T smart-money-shadow python -c "
import os, sys
try:
    import psycopg
    url = os.environ.get('DATABASE_URL', '')
    if not url:
        print('DATABASE_URL not set in container', file=sys.stderr); sys.exit(1)
    with psycopg.connect(url) as c, c.cursor() as cur:
        windows = [('1h', '1 hour'), ('6h', '6 hours'), ('24h', '24 hours')]
        print(f\"{'window':>6} {'paper_open':>10} {'paper_closed':>12} {'skips':>6} {'latest':>22}\")
        for label, iv in windows:
            cur.execute(f\"select count(*) filter (where closed_at is null), count(*) filter (where closed_at is not null), max(opened_at) from sm_paper_trades where opened_at > now() - interval '{iv}'\")
            op, cl, last = cur.fetchone()
            cur.execute(f\"select count(*) from sm_skipped_signals where created_at > now() - interval '{iv}'\")
            sk, = cur.fetchone()
            print(f\"{label:>6} {op:>10} {cl:>12} {sk:>6} {str(last):>22}\")

        print()
        cur.execute(\"select reason, count(*) from sm_skipped_signals where created_at > now() - interval '24 hours' group by reason order by 2 desc\")
        reasons = cur.fetchall()
        if reasons:
            print('skipped signals by reason (24h):')
            for r, n in reasons:
                print(f'  {r}: {n}')

        print()
        cur.execute(\"select side, count(*) from sm_wallet_positions group by side order by 1\")
        pos_sides = cur.fetchall()
        print('wallet_positions tracked:')
        for s, n in pos_sides:
            print(f'  {s}: {n}')

        cur.execute(\"select count(distinct wallet_id) from sm_wallet_positions\")
        (n_wallets,) = cur.fetchone()
        print(f'  distinct wallets with state: {n_wallets}')

        print()
        cur.execute(\"select percentile_disc(0.5) within group (order by signal_latency_ms), percentile_disc(0.95) within group (order by signal_latency_ms), percentile_disc(0.99) within group (order by signal_latency_ms), count(*) from sm_paper_trades where opened_at > now() - interval '24 hours' and signal_latency_ms is not null\")
        p50, p95, p99, n = cur.fetchone()
        if n:
            print(f'latency (24h, n={n}): p50={p50}ms p95={p95}ms p99={p99}ms')
        else:
            print('latency (24h): no samples yet')
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
