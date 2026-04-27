#!/bin/bash
# ============================================================
# All Money Back Me Home — Deploy Script
# Run: bash scripts/deploy.sh
# ============================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=== AMBMH Deploy ==="
echo "Directory: $PROJECT_DIR"

# 1. Pull latest code
#
# R126: 在 git pull 前偵測 working tree 是否 dirty。R122-followup 的
# GitHub Actions deploy 失敗就是這個 pattern：VPS 端有 uncommitted local
# changes (operator 在 R122 incident 中手動 chmod +x scripts/polymarket
# _pipeline.sh 修問題)，當 R122-followup 提交追蹤同檔案的 mode 變更後，
# git pull 因 'local changes would be overwritten' 中止 → deploy 在 step 1
# 死掉，後面所有 R125 verify_deploy 防護鏈都跑不到。
#
# 處理：dirty tree → 自動 git stash (帶 timestamp 標籤好回追) + 繼續 pull。
# 任何 stash 內容會留在 stash list 給 operator 後續決定 (drop/pop/diff)。
echo "[1/7] Pulling latest code..."
# 只檢查 tracked files 的 modifications (含 staged + unstaged)；untracked
# files (cron 寫的 journal/state/lock) 不會 block git pull，不需 stash。
if ! git diff --quiet HEAD 2>/dev/null; then
    stash_label="auto-deploy-stash-$(date -u '+%Y%m%dT%H%M%SZ')"
    echo "  ⚠️  R126: tracked files modified locally — auto-stashing as '$stash_label'"
    git diff --stat HEAD | sed 's/^/    /'
    git stash push -m "$stash_label" || {
        echo "  ✗ git stash failed — manual intervention required"
        echo "    Recover: cd /opt/ambmh && git status, then commit or git checkout -- <files>"
        exit 1
    }
    echo "  ✓ stashed; list via: git stash list | grep $stash_label"
fi
git pull origin main

# R132: re-exec self after git pull. Bash 把 small scripts (≤8KB) 整個
# buffer 進 memory, git pull 替換 deploy.sh 在 disk 上之後, 後面所有 step
# 還是執行 OLD in-memory content → 第一次 deploy 永遠跑舊版邏輯,
# 修 bug 必須 push 兩次才生效 (R131 deploy log 21 行 ERR 就是這個 bug 的
# fingerprint - R131 fix 在 disk 上但 R130 in-memory 邏輯還在跑).
#
# 解法: pull 完 exec 自己, NEW deploy.sh 接手. DEPLOY_REEXEC_DONE env
# 防無限 loop (子 exec 帶這個 env 就 skip 這段).
if [ -z "${DEPLOY_REEXEC_DONE:-}" ]; then
    echo "  ↻ R132: re-exec deploy.sh to load NEW post-pull content"
    export DEPLOY_REEXEC_DONE=1
    exec bash "$0" "$@"
fi

# 2. Check .env
if [ ! -f .env ]; then
    echo "ERROR: .env file not found. Copy from .env.example and configure."
    exit 1
fi

# R64: Pre-flight validation — catch silent compose breakages BEFORE
# spending 5 minutes on a build that lands in a half-broken state.
# Detects duplicate environment blocks, missing required mounts,
# .env.example drift, cross-service env inconsistency. Fail-fast.
echo "[2/7] Running preflight checks..."
if ! python3 scripts/preflight_check.py; then
    echo "ERROR: preflight check failed — fix issues above before deploying."
    exit 1
fi

# 3. Build images
echo "[3/7] Building Docker images..."
docker compose -f docker-compose.prod.yml build

# 4. Run database migrations.
#
# Three-tier fallback:
#   1. supabase CLI if available (team's preferred path)
#   2. psycopg inside already-running smart-money container (zero extra deps,
#      just needs DATABASE_URL set in compose env)
#   3. skip + warn (first-ever deploy, smart-money not yet up)
#
# All migration files in supabase/migrations/*.sql MUST be idempotent
# (create table if not exists / add column if not exists); they are replayed
# on every deploy without harm.
if command -v supabase &> /dev/null; then
    echo "[4/7] Running database migrations via supabase CLI..."
    supabase db push || echo "  Migration skipped (check Supabase config)"
elif docker compose -f docker-compose.prod.yml ps smart-money 2>/dev/null | grep -q " Up "; then
    echo "[4/7] Running database migrations via smart-money container..."
    # R131: pre-check DATABASE_URL once before looping. 之前每個 .sql 都進
    # container 重 import psycopg 跑 'if not url: print(...); sys.exit(1)',
    # operator 沒設 SM_DATABASE_URL 時 → 21 行 'ERR: DATABASE_URL not set'
    # spam, 真實根因被淹沒。一次檢查直接 skip 整個 batch + 給明確 hint。
    db_url_check=$(docker compose -f docker-compose.prod.yml exec -T smart-money \
        sh -c 'echo "${DATABASE_URL:-MISSING}"' 2>/dev/null | tr -d '\r')
    if [ "$db_url_check" = "MISSING" ] || [ -z "$db_url_check" ]; then
        echo "  ⚠️  R131: smart-money container 沒 DATABASE_URL — skip migrations"
        echo "       Fix: 在 .env 設 SM_DATABASE_URL=postgresql://... (見 .env.example)"
        echo "       (smart_money phase 0 stub 暫不需 DB; 但若想啟用 smart_money table"
        echo "        相關 features 必須設這個)"
    else
        migration_count=0
        migration_failed=0
        for f in supabase/migrations/*.sql; do
            [ -f "$f" ] || continue
            migration_count=$((migration_count + 1))
            echo "  applying $(basename "$f")..."
            if ! cat "$f" | docker compose -f docker-compose.prod.yml exec -T smart-money python -c '
import sys, psycopg, os
sql = sys.stdin.read()
url = os.environ["DATABASE_URL"]   # R131: pre-check 已驗證存在
with psycopg.connect(url, autocommit=True) as c, c.cursor() as cur:
    cur.execute(sql)
'; then
                echo "    FAILED — continuing"
                migration_failed=$((migration_failed + 1))
            fi
        done
        echo "  migrations: $migration_count attempted, $migration_failed failed"
    fi
else
    echo "[4/7] No supabase CLI and smart-money container not running —"
    echo "      skipping migrations. Next deploy will pick them up once"
    echo "      smart-money comes online."
fi

# 5. Apply changes without full teardown.
#
# `up -d --build` lets docker compose reconcile: only services whose image
# OR compose definition changed get recreated. Unchanged services keep running.
#
# Why this matters: Supertrend (freqtrade container) is signal-rare — every
# full restart wipes its in-memory signal history. We used to `down && up -d`
# which disturbed freqtrade on every unrelated deploy and cost trades.
echo "[5/7] Applying changes (reconcile, only recreate what changed)..."

# R139: Pre-up cleanup of stuck containers. R116 reactive cleanup (post-
# verify_deploy) wasn't enough — `docker compose up` itself fails with
# "name conflict" or "Error while Stopping" if previous deploy left
# containers half-stopped (這個 session 又 hit 一次, 看 commit ff4d3b4
# deploy 24979783816). Detect & force-remove ONLY containers in stuck
# states (dead/removing/created/exited), preserving running ones (don't
# disturb freqtrade in-memory state per R47 design rule).
echo "  Pre-checking for stuck containers (R139 — orphan cleanup)..."
for c in $(docker ps -a --filter "name=ambmh-" --format '{{.Names}}' 2>/dev/null); do
    state=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null)
    case "$state" in
        running|paused|restarting)
            ;;  # leave alone
        dead|removing|created|exited)
            echo "    R139: force-removing stuck $c (state=$state)"
            docker rm -f "$c" 2>/dev/null || true
            ;;
        *)
            echo "    R139: $c state=$state — leaving alone"
            ;;
    esac
done

# --wait blocks until all services reach their healthy state (or timeout).
# This guarantees nginx reload below sees the *final* upstream IPs.
docker compose -f docker-compose.prod.yml up -d --build --remove-orphans --wait --wait-timeout 120

# After web/api get recreated their IPs change; nginx's upstream DNS cache
# becomes stale → 502 until next resolve. Force nginx to refresh.
# `nginx -s reload` is sub-second and keeps the container alive.
echo "  Reloading nginx to refresh upstream DNS..."
docker compose -f docker-compose.prod.yml exec -T nginx nginx -s reload 2>/dev/null || \
    docker compose -f docker-compose.prod.yml restart nginx

# 6. Health check
echo "[6/7] Waiting for services..."
sleep 5

if curl -sf http://localhost/health > /dev/null 2>&1; then
    echo "  API health: OK"
else
    echo "  API health: FAILED (checking logs...)"
    docker compose -f docker-compose.prod.yml logs --tail=20 api
fi

if curl -sf http://localhost > /dev/null 2>&1; then
    echo "  Web: OK"
else
    echo "  Web: FAILED (checking logs...)"
    docker compose -f docker-compose.prod.yml logs --tail=20 web
fi

# Freqtrade health — single container on 127.0.0.1:8080
# (legacy two-bot trend/scalp setup retired 2026-Q2)
echo "  Checking freqtrade..."
sleep 3

if [ -f .env ]; then
    FT_USER=$(grep -E '^FT_USER=' .env | cut -d= -f2- || echo freqtrade)
    FT_PASS=$(grep -E '^FT_PASS=' .env | cut -d= -f2- || echo freqtrade)
fi
FT_USER=${FT_USER:-freqtrade}
FT_PASS=${FT_PASS:-freqtrade}

if curl -sf -u "${FT_USER}:${FT_PASS}" "http://127.0.0.1:8080/api/v1/ping" > /dev/null 2>&1; then
    echo "  freqtrade: OK"
else
    echo "  freqtrade: STARTING (docker logs ambmh-freqtrade-1)"
fi

# R60: ensure freqtrade bot is in "running" state, not just "container up".
#
# CRITICAL: config_dry.json sets initial_state="running" but freqtrade's
# runtime ignores it (verified 2026-04-25 on VPS — Configuration.from_files
# loads the field, but Worker still boots into stopped). This curl /start
# is the SOLE reliable mechanism for getting the bot scanning after a
# container recreate. Without it, container Up + bot stopped = silent zero
# trading, no signals fired, journal empty.
#
# Also handles: operator manually stopped the bot via UI/API; that state
# survives container recreation, so we re-affirm at every deploy.
echo "  Verifying freqtrade bot state..."
sleep 2
ft_state=$(curl -sf -u "${FT_USER}:${FT_PASS}" \
    "http://127.0.0.1:8080/api/v1/show_config" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state",""))' \
    2>/dev/null || echo "unknown")
case "$ft_state" in
    running)
        echo "  freqtrade bot: running ✓"
        ;;
    stopped)
        echo "  freqtrade bot: stopped — issuing /start..."
        curl -sf -u "${FT_USER}:${FT_PASS}" \
            -X POST "http://127.0.0.1:8080/api/v1/start" > /dev/null \
            && echo "  freqtrade bot: started ✓" \
            || echo "  freqtrade bot: /start failed (check container logs)"
        ;;
    *)
        echo "  freqtrade bot: state=$ft_state — not querying (likely still booting)"
        ;;
esac

# R125: Post-deploy verification (R109 同模式 — 但 main deploy 路徑之前漏裝)
#
# Background: redeploy_service.sh (R109/R116) 早已整合 verify_deploy + retry +
# Telegram alert，但這個 deploy.sh（GitHub Actions auto-deploy 走的路徑）卻
# 從未呼叫 verify_deploy。後果：每次 push to main → Actions 自動 deploy →
# 但 R104-class silent failure (guards 沒 import / env 沒對齊 / +x 缺) 完全
# 不會被偵測，必須等下次 operator 手動跑 verify_deploy 才知道。R125 補齊。
#
# Skip via DEPLOY_SKIP_VERIFY=1 if absolutely needed (default: always run).
echo ""
echo "[7/7] Post-deploy verification (R125)..."
if [ "${DEPLOY_SKIP_VERIFY:-0}" = "1" ]; then
    echo "  DEPLOY_SKIP_VERIFY=1 — skipping verify_deploy.sh"
elif [ -x scripts/verify_deploy.sh ]; then
    set +e
    bash scripts/verify_deploy.sh > /tmp/verify_deploy.log 2>&1
    verify_rc=$?
    set -e
    # R116 同模式: 第一次 fail 可能是 container 還在 starting，retry 一次
    if [ "$verify_rc" -ne 0 ]; then
        echo "  ⏳ verify_deploy first-pass failed — waiting 30s and retrying..."
        sleep 30
        set +e
        bash scripts/verify_deploy.sh > /tmp/verify_deploy.log 2>&1
        verify_rc=$?
        set -e
    fi
    if [ "$verify_rc" -eq 0 ]; then
        echo "  ✓ verify_deploy passed"
    else
        echo ""
        echo "  ✗ verify_deploy FAILED — output:"
        cat /tmp/verify_deploy.log | sed 's/^/    /'
        # Telegram alert (best-effort)
        if [ -f .env ]; then
            TG_TOKEN=$(grep -E '^TELEGRAM_TOKEN=' .env | cut -d= -f2- || true)
            TG_CHAT=$(grep -E '^TELEGRAM_CHAT_ID=' .env | cut -d= -f2- || true)
            if [ -n "${TG_TOKEN:-}" ] && [ -n "${TG_CHAT:-}" ]; then
                MSG="🚨 R125: GitHub Actions auto-deploy succeeded BUT verify_deploy FAILED. Possible R104-class silent failure. Check /tmp/verify_deploy.log on VPS."
                curl -sS -m 10 \
                    "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
                    -d "chat_id=${TG_CHAT}" --data-urlencode "text=${MSG}" \
                    > /dev/null 2>&1 || echo "    (telegram alert send failed)"
            fi
        fi
        echo ""
        echo "  ⚠️  Deploy succeeded but verification flagged issues."
        echo "     Manual review required (see /tmp/verify_deploy.log)."
        # Non-zero exit so GitHub Actions UI shows red
        exit 3
    fi
else
    echo "  scripts/verify_deploy.sh not executable — skipping (run chmod +x)"
fi

echo ""
echo "=== Deploy complete! ==="
docker compose -f docker-compose.prod.yml ps
