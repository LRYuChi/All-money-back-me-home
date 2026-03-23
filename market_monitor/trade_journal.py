"""統一交易日誌 — JSONL 本地 + Supabase 遠端雙寫。

所有策略（SMCTrend、Supertrend、BBSqueeze 等）共用此模組記錄交易。
本地 JSONL 確保即時寫入，Supabase 確保持久化和查詢能力。
Supabase 寫入失敗不影響交易執行（fire-and-forget）。

使用方式:
    from market_monitor.trade_journal import TradeJournal
    journal = TradeJournal()
    journal.log_entry({...})
    journal.log_exit({...})
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
_JOURNAL_PATH = _DATA_DIR / "trade_journal.jsonl"

# Supabase config (optional)
_SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
_SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


class TradeJournal:
    """統一交易日誌 — JSONL 本地 + Supabase 遠端雙寫。"""

    def log_entry(
        self,
        strategy: str,
        pair: str,
        side: str,
        rate: float,
        stake: float,
        leverage: float,
        confidence: float = None,
        regime: str = None,
        entry_reasons: dict = None,
        indicators: dict = None,
    ):
        """記錄進場日誌。"""
        record = {
            "event": "ENTRY",
            "ts": datetime.now(timezone.utc).isoformat(),
            "strategy": strategy,
            "pair": pair,
            "side": side,
            "entry_price": round(rate, 6),
            "stake_usd": round(stake, 2),
            "leverage": round(leverage, 2),
            "confidence": round(confidence, 4) if confidence is not None else None,
            "regime": regime,
            "entry_reasons": entry_reasons or {},
            "indicators": indicators or {},
        }
        self._write_jsonl(record)
        self._write_supabase(record)
        logger.info(
            "JOURNAL ENTRY: %s %s %s @ %.4f | stake=%.1f lev=%.1f conf=%s | %s",
            strategy, pair, side, rate, stake, leverage,
            f"{confidence:.2f}" if confidence else "N/A",
            json.dumps(entry_reasons or {}, ensure_ascii=False)[:100],
        )

    def log_exit(
        self,
        strategy: str,
        pair: str,
        side: str,
        rate: float,
        exit_reason: str,
        pnl_pct: float,
        pnl_usd: float,
        duration_min: float,
        r_multiple: float = None,
        confidence: float = None,
    ):
        """記錄出場日誌。"""
        record = {
            "event": "EXIT",
            "ts": datetime.now(timezone.utc).isoformat(),
            "strategy": strategy,
            "pair": pair,
            "side": side,
            "exit_price": round(rate, 6),
            "exit_reason": exit_reason,
            "pnl_pct": round(pnl_pct, 4),
            "pnl_usd": round(pnl_usd, 4),
            "duration_min": round(duration_min, 1),
            "r_multiple": round(r_multiple, 2) if r_multiple is not None else None,
            "confidence": round(confidence, 4) if confidence is not None else None,
        }
        self._write_jsonl(record)
        self._write_supabase(record)
        emoji = "💰" if pnl_pct >= 0 else "🔴"
        logger.info(
            "JOURNAL EXIT: %s %s %s %s @ %.4f | PnL=%+.2f%% ($%+.2f) | R=%s | %s",
            emoji, strategy, pair, side, rate, pnl_pct, pnl_usd,
            f"{r_multiple:.1f}" if r_multiple else "N/A", exit_reason,
        )

    def _write_jsonl(self, record: dict):
        """寫入本地 JSONL（即時，無網路依賴）。"""
        try:
            _JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_JOURNAL_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            logger.warning("JSONL 寫入失敗: %s", e)

    def _write_supabase(self, record: dict):
        """寫入 Supabase trade_log 表（fire-and-forget）。"""
        if not _SUPABASE_URL or not _SUPABASE_KEY:
            return

        try:
            url = f"{_SUPABASE_URL}/rest/v1/trade_log"
            # Build payload matching table schema
            payload = {
                "event": record["event"],
                "strategy": record["strategy"],
                "pair": record["pair"],
                "side": record["side"],
                "ts": record["ts"],
            }

            if record["event"] == "ENTRY":
                payload.update({
                    "entry_price": record.get("entry_price"),
                    "stake_usd": record.get("stake_usd"),
                    "leverage": record.get("leverage"),
                    "confidence": record.get("confidence"),
                    "regime": record.get("regime"),
                    "entry_reasons": json.dumps(record.get("entry_reasons", {})),
                    "indicators": json.dumps(record.get("indicators", {})),
                })
            elif record["event"] == "EXIT":
                payload.update({
                    "exit_price": record.get("exit_price"),
                    "exit_reason": record.get("exit_reason"),
                    "pnl_pct": record.get("pnl_pct"),
                    "pnl_usd": record.get("pnl_usd"),
                    "duration_min": record.get("duration_min"),
                    "r_multiple": record.get("r_multiple"),
                })

            data = json.dumps(payload, default=str).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "apikey": _SUPABASE_KEY,
                    "Authorization": f"Bearer {_SUPABASE_KEY}",
                    "Prefer": "return=minimal",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status in (200, 201):
                    logger.debug("Supabase trade_log 寫入成功")
        except Exception as e:
            # Fire-and-forget: 不影響交易執行
            logger.debug("Supabase 寫入跳過: %s", e)


# 單例
_journal = TradeJournal()


def get_journal() -> TradeJournal:
    """取得共用 TradeJournal 實例。"""
    return _journal
