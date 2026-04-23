#!/usr/bin/env bash
# WFO segment validation for V1 (SupertrendStrategy)
#
# 切 200 日 (2025-09-01 ~ 2026-03-19) 為 6 段，每段 ~33 日，獨立 backtest.
# 目的：驗證 V1 +28.80% 是否分散在多段，還是集中在 1-2 段（regime sensitivity）。
#
# 此腳本必須在 VPS 上跑。輸出寫到 /tmp/wfo_<seg>_<from>_<to>.log
# 完成後逐段拉 backtest_results 回本地用 analyze_backtest.py 分析。
#
# Usage (on VPS):
#   bash /tmp/wfo_segment_v1.sh

set -e

SEGMENTS=(
    "20250901-20251004"  # seg1: Sep 1-Oct 4 (~33 days)
    "20251005-20251107"  # seg2: Oct 5-Nov 7
    "20251108-20251211"  # seg3: Nov 8-Dec 11
    "20251212-20260114"  # seg4: Dec 12-Jan 14
    "20260115-20260217"  # seg5: Jan 15-Feb 17
    "20260218-20260319"  # seg6: Feb 18-Mar 19
)

PAIRS="BTC/USDT:USDT ETH/USDT:USDT AVAX/USDT:USDT NEAR/USDT:USDT ATOM/USDT:USDT ADA/USDT:USDT DOT/USDT:USDT"
COMPOSE="docker compose -f /opt/ambmh/docker-compose.prod.yml"

for tr in "${SEGMENTS[@]}"; do
    seg_id="${tr/-/_}"
    log="/tmp/wfo_${seg_id}.log"
    echo "[$(date)] Starting segment ${tr} → ${log}"
    $COMPOSE exec -T freqtrade freqtrade backtesting \
        --strategy SupertrendStrategy \
        -c /freqtrade/config/config_dry.json \
        --strategy-path /freqtrade/user_data/strategies \
        --timeframe 15m \
        --timerange "${tr}" \
        --pairs $PAIRS \
        --export trades \
        > "$log" 2>&1
    echo "[$(date)] Segment ${tr} done"
done

echo "All 6 segments complete. Latest result files:"
$COMPOSE exec -T freqtrade ls -lt /freqtrade/user_data/backtest_results/ | head -15
