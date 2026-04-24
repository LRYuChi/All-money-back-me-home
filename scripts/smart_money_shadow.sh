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
        # Uses whatever Store the container is configured for (Supabase REST
        # or direct Postgres), so it works whether DATABASE_URL is set or not.
        docker compose -f "$COMPOSE_FILE" exec -T smart-money-shadow python -c "
from datetime import datetime, timedelta, timezone
from smart_money.config import settings
from smart_money.store.db import build_store

store = build_store(settings)
now = datetime.now(timezone.utc)
windows = [('1h', 1), ('6h', 6), ('24h', 24)]

def q(since):
    trades = store.list_paper_trades(since=since)
    # skipped_signals are written via record_skipped_signal; no list reader in
    # the Protocol. For Supabase fall back to direct REST.
    return trades

print(f\"{'window':>6} {'paper_open':>10} {'paper_closed':>12} {'latest':>26}\")
for label, hours in windows:
    since = now - timedelta(hours=hours)
    trades = store.list_paper_trades(since=since)
    op = sum(1 for t in trades if t.closed_at is None)
    cl = sum(1 for t in trades if t.closed_at is not None)
    last = max((t.opened_at for t in trades), default=None)
    print(f\"{label:>6} {op:>10} {cl:>12} {str(last):>26}\")

# Latency percentiles (24h) — compute client-side, works for any store.
trades_24h = store.list_paper_trades(since=now - timedelta(hours=24))
lats = sorted(t.signal_latency_ms for t in trades_24h if t.signal_latency_ms is not None)
if lats:
    def pct(p): return lats[min(int(len(lats) * p), len(lats) - 1)]
    print()
    print(f'latency (24h, n={len(lats)}): p50={pct(0.5)}ms p95={pct(0.95)}ms p99={pct(0.99)}ms')
else:
    print()
    print('latency (24h): no paper trades yet')

# Skipped signals + wallet_positions — Supabase REST direct query.
client = getattr(store, '_client', None)
if client is not None:
    print()
    since_iso = (now - timedelta(hours=24)).isoformat()
    res = client.table('sm_skipped_signals').select('reason').gte('created_at', since_iso).execute()
    from collections import Counter
    counts = Counter(r['reason'] for r in (res.data or []))
    if counts:
        print('skipped signals by reason (24h):')
        for r, n in counts.most_common():
            print(f'  {r}: {n}')
    else:
        print('skipped signals (24h): 0')

    print()
    res = client.table('sm_wallet_positions').select('side').execute()
    by_side = Counter(r['side'] for r in (res.data or []))
    print('wallet_positions tracked:')
    for s, n in sorted(by_side.items()):
        print(f'  {s}: {n}')
    print(f'  total rows: {sum(by_side.values())}')
else:
    print()
    print('(skipped_signals + wallet_positions summary requires SupabaseStore client)')
"
        ;;
    *)
        echo "error: unknown command '$CMD'" >&2
        exit 64
        ;;
esac
