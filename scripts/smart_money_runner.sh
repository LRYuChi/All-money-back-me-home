#!/usr/bin/env bash
# smart_money_runner.sh — dispatch smart_money CLI subcommands inside the
# dedicated `smart-money` container.
#
# Usage:
#     scripts/smart_money_runner.sh {scan|rank|backtest|seed} [args...]
#
# Example:
#     scripts/smart_money_runner.sh rank --top 20
#     scripts/smart_money_runner.sh backtest \
#         --cutoff 2025-10-31 --cutoff 2026-01-31 --cutoff 2026-04-20 \
#         --forward-months 6 --top 20

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "usage: $0 {scan|rank|backtest|seed} [args...]" >&2
    exit 64
fi

CMD="$1"
shift

case "$CMD" in
    scan|rank|backtest|seed) ;;
    *)
        echo "error: unknown command '$CMD' (expected: scan|rank|backtest|seed)" >&2
        exit 64
        ;;
esac

PROJECT_ROOT="${PROJECT_ROOT:-/opt/ambmh}"
COMPOSE_FILE="${COMPOSE_FILE:-${PROJECT_ROOT}/docker-compose.prod.yml}"

cd "$PROJECT_ROOT"

exec docker compose -f "$COMPOSE_FILE" exec -T smart-money \
    python -m "smart_money.cli.${CMD}" "$@"
