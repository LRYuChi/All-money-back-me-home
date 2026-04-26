#!/bin/bash
# AI Agent Analysis Pipeline — 三步驟，最省 token
# Cron: 0 0,8,16 * * *
#
# Step 1: data_collector.py  — 純 Python，收集所有數據
# Step 2: summarizer.py      — 純 Python，壓縮為摘要
# Step 3: brain.py (pipeline) — Claude AI，只分析摘要
#
# Token: ~2500/次 (vs 舊架構 ~10000)

cd /opt/ambmh
source .env 2>/dev/null

echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] Pipeline started"

# Step 1: Data Collection (pure Python, ~20s)
echo "Step 1: Collecting data..."
docker exec -e DATA_DIR=/app/data ambmh-api-1 python -m agent.data_collector 2>&1

# Step 2: Summarize (pure Python, <1s)
echo "Step 2: Generating summary..."
docker exec -e DATA_DIR=/app/data ambmh-api-1 python -m agent.summarizer 2>&1

# Step 3: AI Analysis (Claude, ~3s, ~2500 tokens)
echo "Step 3: AI analysis..."
docker exec \
  -e AGENT_MODE=pipeline \
  -e DATA_DIR=/app/data \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -e ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL \
  -e TELEGRAM_TOKEN=$TELEGRAM_TOKEN \
  -e TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID \
  ambmh-api-1 python -m agent.brain 2>&1

echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] Pipeline complete"
