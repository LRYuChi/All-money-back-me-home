#!/usr/bin/env python3
"""決策驗證排程 — 每小時處理到期的驗證任務。"""

import json
import logging
import os
import sys
import urllib.request
import base64
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

FT_API = "http://localhost:8080/api/v1"


def _ft_get(endpoint: str) -> dict | None:
    try:
        user = os.environ.get("FT_USER", "freqtrade")
        pw = os.environ.get("FT_PASS", "freqtrade")
        auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
        req = urllib.request.Request(
            f"{FT_API}/{endpoint}",
            headers={"Authorization": f"Basic {auth}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _get_current_metrics() -> dict:
    """取得當前績效指標作為驗證基準"""
    profit = _ft_get("profit")
    if not profit:
        return {}
    winning = profit.get("winning_trades", 0)
    losing = profit.get("losing_trades", 0)
    total = winning + losing
    return {
        "profit_factor": profit.get("profit_factor", 1.0),
        "win_rate": winning / total if total > 0 else 0.5,
        "max_drawdown": profit.get("max_drawdown_abs", 0),
        "total_trades": total,
    }


def main():
    print(f"🔍 決策驗證 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    try:
        from agent.hallucination_guard import HallucinationGuard
        guard = HallucinationGuard()
    except ImportError as e:
        print(f"匯入失敗: {e}")
        return

    current = _get_current_metrics()
    if not current:
        print("無法取得績效指標，跳過驗證")
        return

    results = guard.run_pending_verifications(current)

    if not results:
        print("無到期驗證任務")
        return

    effective = sum(1 for r in results if r["was_effective"])
    total = len(results)
    print(f"完成 {total} 筆驗證：{effective} 有效 / {total - effective} 無效")

    # Update memory outcomes
    try:
        from agent.memory import AgentMemory
        memory = AgentMemory()
        for r in results:
            memory.update_outcome(
                r["decision_id"],
                {r["check_type"]: r["outcome"]},
                r["was_effective"],
            )
    except Exception as e:
        print(f"回填記憶失敗: {e}")

    # Telegram summary
    try:
        from market_monitor.telegram_zh import send_message
        send_message(
            f"🔍 *決策驗證報告*\n"
            f"完成: {total} 筆\n"
            f"有效: {effective} | 無效: {total - effective}\n"
            f"PF: {current.get('profit_factor', '?'):.2f} | "
            f"WR: {current.get('win_rate', 0)*100:.0f}%"
        )
    except Exception:
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
