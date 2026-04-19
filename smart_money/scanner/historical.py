"""批次拉取鯨魚錢包歷史交易並入庫.

設計重點:
- idempotent:`(wallet_id, hl_trade_id)` 為唯一鍵,重跑不產生 dup
- resume:從 store 現有最後一筆 ts 繼續,不重抓
- 分頁由 `HLClient.get_wallet_trades` 負責,此層只做 orchestration
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from smart_money.scanner.hl_client import HLClient
from smart_money.store.db import TradeStore

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BackfillResult:
    address: str
    trades_inserted: int
    trades_total: int
    start_ts: datetime
    end_ts: datetime
    skipped_reason: str | None = None


def backfill_wallet(
    store: TradeStore,
    client: HLClient,
    address: str,
    *,
    lookback_days: int = 90,
    now: datetime | None = None,
) -> BackfillResult:
    """回補單一錢包過去 N 天交易.

    Resume 邏輯:若 DB 已有該錢包,從最後一筆 ts + 1ms 開始;否則從 lookback_days 前開始.
    """
    now = now or datetime.now(tz=timezone.utc)
    start_boundary = now - timedelta(days=lookback_days)

    wallet = store.upsert_wallet(address, seen_at=now)

    last_ts = store.get_last_trade_ts(wallet.id)
    if last_ts and last_ts >= start_boundary:
        # 續抓:從 last_ts + 1ms 開始
        start_ts = last_ts + timedelta(milliseconds=1)
    else:
        start_ts = start_boundary

    if start_ts >= now:
        logger.info("wallet %s already up-to-date (last=%s)", address, last_ts)
        return BackfillResult(
            address=address,
            trades_inserted=0,
            trades_total=store.count_trades(wallet.id),
            start_ts=start_ts,
            end_ts=now,
            skipped_reason="up-to-date",
        )

    start_ms = int(start_ts.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    trades_iter = client.get_wallet_trades(
        address=address,
        wallet_id=wallet.id,
        start_ms=start_ms,
        end_ms=end_ms,
    )

    # 收集成 list 一次 upsert,較省 round-trip
    trades = list(trades_iter)
    inserted = store.upsert_trades(trades)
    total = store.count_trades(wallet.id)

    logger.info(
        "backfilled %s: window=%s→%s new=%d total=%d",
        address,
        start_ts.isoformat(),
        now.isoformat(),
        inserted,
        total,
    )

    return BackfillResult(
        address=address,
        trades_inserted=inserted,
        trades_total=total,
        start_ts=start_ts,
        end_ts=now,
    )


def backfill_batch(
    store: TradeStore,
    client: HLClient,
    addresses: list[str],
    *,
    lookback_days: int = 90,
    now: datetime | None = None,
    on_progress: "callable | None" = None,
) -> list[BackfillResult]:
    """批次版本.

    on_progress(idx, total, result) 每完成一個錢包呼叫一次,方便 CLI 顯示進度.
    """
    results: list[BackfillResult] = []
    for i, addr in enumerate(addresses, start=1):
        try:
            r = backfill_wallet(store, client, addr, lookback_days=lookback_days, now=now)
        except Exception as exc:  # noqa: BLE001
            logger.error("backfill failed for %s: %s", addr, exc)
            r = BackfillResult(
                address=addr,
                trades_inserted=0,
                trades_total=0,
                start_ts=datetime.now(tz=timezone.utc),
                end_ts=datetime.now(tz=timezone.utc),
                skipped_reason=f"error: {exc}",
            )
        results.append(r)
        if on_progress:
            on_progress(i, len(addresses), r)
    return results


__all__ = ["BackfillResult", "backfill_batch", "backfill_wallet"]
