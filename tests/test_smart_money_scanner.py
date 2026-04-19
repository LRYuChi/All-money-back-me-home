"""Unit tests for smart_money.scanner + store (Phase 1).

所有測試以 InMemoryStore + FakeInfo 跑,不觸網路.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from smart_money.scanner.hl_client import HLClient, _fill_to_trade, _parse_dir
from smart_money.scanner.historical import backfill_batch, backfill_wallet
from smart_money.scanner.seeds import is_valid_address, load_seed_file
from smart_money.store.db import InMemoryStore
from smart_money.store.schema import Trade


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #
@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def now_utc() -> datetime:
    return datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)


def make_fill(
    *,
    tid: int,
    coin: str = "BTC",
    px: float = 50000.0,
    sz: float = 0.1,
    direction: str = "Open Long",
    closed_pnl: float | None = None,
    fee: float = 0.05,
    ts_ms: int = 1700000000000,
) -> dict:
    return {
        "tid": tid,
        "coin": coin,
        "px": str(px),
        "sz": str(sz),
        "dir": direction,
        "closedPnl": (str(closed_pnl) if closed_pnl is not None else None),
        "fee": str(fee),
        "time": ts_ms,
        "hash": f"0x{tid:064x}",
        "oid": tid,
        "side": "B" if "Long" in direction and "Open" in direction else "A",
    }


class FakeInfo:
    """Minimal InfoLike that serves pre-canned fills."""

    def __init__(self, fills_by_address: dict[str, list[dict]]):
        self._fills = fills_by_address
        self.calls: list[tuple] = []

    def user_fills_by_time(
        self,
        address: str,
        start_time: int,
        end_time: int | None = None,
        aggregate_by_time: bool | None = False,
    ) -> list[dict]:
        self.calls.append((address, start_time, end_time))
        fills = self._fills.get(address, [])
        out = [f for f in fills if start_time <= f["time"] <= (end_time or 10**15)]
        # HL pagination: 每次最多 FILLS_PAGE_MAX(2000);本測試場景筆數少,一次回完
        return out[:2000]

    def user_state(self, address: str, dex: str = "") -> dict:
        return {"address": address, "dex": dex}

    def all_mids(self, dex: str = "") -> dict:
        return {"BTC": "50000", "ETH": "3000"}


# ------------------------------------------------------------------ #
# _parse_dir
# ------------------------------------------------------------------ #
@pytest.mark.parametrize(
    "direction,expected",
    [
        ("Open Long", ("long", "open")),
        ("Close Long", ("long", "close")),
        ("Open Short", ("short", "open")),
        ("Close Short", ("short", "close")),
        ("Long > Short", ("short", "open")),   # 反手到 short
        ("Short > Long", ("long", "open")),
    ],
)
def test_parse_dir_valid(direction, expected):
    assert _parse_dir(direction) == expected


def test_parse_dir_invalid_spot():
    from smart_money.scanner.hl_client import HLClientError

    with pytest.raises(HLClientError):
        _parse_dir("Buy")


# ------------------------------------------------------------------ #
# _fill_to_trade
# ------------------------------------------------------------------ #
def test_fill_to_trade_basic():
    wid = uuid4()
    fill = make_fill(tid=1, direction="Open Long", px=50000, sz=0.5, ts_ms=1_700_000_000_000)
    trade = _fill_to_trade(fill, wid)
    assert trade is not None
    assert trade.wallet_id == wid
    assert trade.hl_trade_id == "1"
    assert trade.symbol == "BTC"
    assert trade.side == "long"
    assert trade.action == "open"
    assert trade.size == 0.5
    assert trade.price == 50000
    assert trade.pnl is None
    assert trade.ts == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)


def test_fill_to_trade_close_with_pnl():
    wid = uuid4()
    fill = make_fill(tid=2, direction="Close Long", closed_pnl=123.45)
    trade = _fill_to_trade(fill, wid)
    assert trade is not None
    assert trade.action == "close"
    assert trade.pnl == 123.45


def test_fill_to_trade_skips_spot():
    wid = uuid4()
    fill = make_fill(tid=3, direction="Buy")
    assert _fill_to_trade(fill, wid) is None


def test_fill_to_trade_skips_missing_ts():
    wid = uuid4()
    fill = make_fill(tid=4, ts_ms=0)
    assert _fill_to_trade(fill, wid) is None


# ------------------------------------------------------------------ #
# HLClient.get_wallet_trades (pagination)
# ------------------------------------------------------------------ #
def test_get_wallet_trades_single_page():
    wid = uuid4()
    addr = "0x" + "a" * 40
    fills = [make_fill(tid=i, ts_ms=1_700_000_000_000 + i * 1000) for i in range(5)]
    info = FakeInfo({addr: fills})
    client = HLClient(info, min_interval_sec=0.0, sleep_fn=lambda _: None)

    trades = list(client.get_wallet_trades(addr, wid, start_ms=0, end_ms=10**15))
    assert len(trades) == 5
    assert all(isinstance(t, Trade) for t in trades)
    assert [t.hl_trade_id for t in trades] == ["0", "1", "2", "3", "4"]
    # 單次 API call 即拉完(因為 < page size)
    assert len(info.calls) == 1


def test_get_wallet_trades_handles_empty():
    wid = uuid4()
    addr = "0x" + "b" * 40
    info = FakeInfo({addr: []})
    client = HLClient(info, min_interval_sec=0.0, sleep_fn=lambda _: None)

    trades = list(client.get_wallet_trades(addr, wid, start_ms=0, end_ms=10**15))
    assert trades == []


def test_hl_client_rate_limit_retries():
    wid = uuid4()
    addr = "0x" + "c" * 40

    call_count = {"n": 0}

    class FlakeyInfo:
        def user_fills_by_time(self, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise RuntimeError("429 Too Many Requests")
            return [make_fill(tid=1)]

        def user_state(self, *a, **k):
            return {}

        def all_mids(self, *a, **k):
            return {}

    sleeps: list[float] = []
    client = HLClient(
        FlakeyInfo(),
        min_interval_sec=0.0,
        max_retries=5,
        sleep_fn=sleeps.append,
    )
    trades = list(client.get_wallet_trades(addr, wid, start_ms=0, end_ms=10**15))
    assert len(trades) == 1
    # 退避 2 次後成功
    assert call_count["n"] == 3
    assert len(sleeps) >= 2


# ------------------------------------------------------------------ #
# InMemoryStore
# ------------------------------------------------------------------ #
def test_store_upsert_wallet_idempotent(store, now_utc):
    w1 = store.upsert_wallet("0xabc", seen_at=now_utc)
    w2 = store.upsert_wallet("0xabc", seen_at=now_utc + timedelta(hours=1))
    assert w1.id == w2.id
    assert w2.last_active_at == now_utc + timedelta(hours=1)
    assert len(store.list_wallets()) == 1


def test_store_upsert_trades_idempotent(store, now_utc):
    w = store.upsert_wallet("0xabc", seen_at=now_utc)
    trades = [
        Trade(
            wallet_id=w.id,
            hl_trade_id=str(i),
            symbol="BTC",
            side="long",
            action="open",
            size=0.1,
            price=50000,
            pnl=None,
            fee=0.05,
            ts=now_utc + timedelta(minutes=i),
        )
        for i in range(3)
    ]
    assert store.upsert_trades(trades) == 3
    assert store.upsert_trades(trades) == 0        # 重跑零新增
    assert store.count_trades(w.id) == 3


def test_store_get_trades_time_filter(store, now_utc):
    w = store.upsert_wallet("0xabc", seen_at=now_utc)
    trades = [
        Trade(w.id, str(i), "BTC", "long", "open", 0.1, 50000, None, 0.05, now_utc + timedelta(days=i))
        for i in range(5)
    ]
    store.upsert_trades(trades)
    since = now_utc + timedelta(days=2)
    filtered = store.get_trades(w.id, since=since)
    assert len(filtered) == 3
    assert all(t.ts >= since for t in filtered)


def test_store_add_tag(store, now_utc):
    w = store.upsert_wallet("0xabc", seen_at=now_utc)
    store.add_tag(w.id, "whitelisted")
    store.add_tag(w.id, "whitelisted")   # idempotent
    store.add_tag(w.id, "watchlist")
    assert w.tags == ["whitelisted", "watchlist"]


# ------------------------------------------------------------------ #
# backfill_wallet
# ------------------------------------------------------------------ #
def test_backfill_wallet_fresh(store, now_utc):
    addr = "0x" + "d" * 40
    fills = [
        make_fill(tid=i, ts_ms=int((now_utc - timedelta(days=5) + timedelta(hours=i)).timestamp() * 1000))
        for i in range(10)
    ]
    info = FakeInfo({addr: fills})
    client = HLClient(info, min_interval_sec=0.0, sleep_fn=lambda _: None)

    result = backfill_wallet(store, client, addr, lookback_days=90, now=now_utc)
    assert result.trades_inserted == 10
    assert result.trades_total == 10
    assert result.skipped_reason is None


def test_backfill_wallet_resume(store, now_utc):
    """跑兩次,第二次只抓新增."""
    addr = "0x" + "e" * 40
    first_fills = [
        make_fill(tid=i, ts_ms=int((now_utc - timedelta(days=3) + timedelta(hours=i)).timestamp() * 1000))
        for i in range(5)
    ]
    all_fills = first_fills + [
        make_fill(tid=100 + i, ts_ms=int((now_utc - timedelta(hours=12 - i)).timestamp() * 1000))
        for i in range(3)
    ]
    info = FakeInfo({addr: all_fills})
    client = HLClient(info, min_interval_sec=0.0, sleep_fn=lambda _: None)

    # 第一次只傳前 5 筆 fills
    info._fills = {addr: first_fills}
    r1 = backfill_wallet(store, client, addr, lookback_days=90, now=now_utc)
    assert r1.trades_inserted == 5

    # 第二次 server 有 8 筆 fills,只新增 3 筆
    info._fills = {addr: all_fills}
    r2 = backfill_wallet(store, client, addr, lookback_days=90, now=now_utc + timedelta(hours=1))
    assert r2.trades_inserted == 3
    assert r2.trades_total == 8


def test_backfill_wallet_error_propagates(store, now_utc):
    addr = "0x" + "f" * 40

    class BrokenInfo:
        def user_fills_by_time(self, *a, **k):
            raise RuntimeError("boom")

        def user_state(self, *a, **k):
            return {}

        def all_mids(self, *a, **k):
            return {}

    client = HLClient(
        BrokenInfo(), min_interval_sec=0.0, max_retries=2, sleep_fn=lambda _: None,
    )
    with pytest.raises(RuntimeError, match="boom"):
        backfill_wallet(store, client, addr, lookback_days=30, now=now_utc)


def test_backfill_batch_survives_single_failure(store, now_utc):
    ok_addr = "0x" + "1" * 40
    bad_addr = "0x" + "2" * 40

    class SelectiveFakeInfo(FakeInfo):
        def user_fills_by_time(self, address, *a, **k):
            if address == bad_addr:
                raise RuntimeError("fail just this one")
            return super().user_fills_by_time(address, *a, **k)

    info = SelectiveFakeInfo({ok_addr: [make_fill(tid=1, ts_ms=int(now_utc.timestamp() * 1000) - 1000)]})
    client = HLClient(info, min_interval_sec=0.0, max_retries=2, sleep_fn=lambda _: None)

    results = backfill_batch(
        store, client, [ok_addr, bad_addr], lookback_days=1, now=now_utc,
    )
    assert len(results) == 2
    assert results[0].trades_inserted == 1
    assert results[1].skipped_reason is not None and "error" in results[1].skipped_reason


# ------------------------------------------------------------------ #
# seeds.py
# ------------------------------------------------------------------ #
@pytest.mark.parametrize(
    "addr,ok",
    [
        ("0x" + "a" * 40, True),
        ("0x" + "A" * 40, True),
        ("0xabc", False),
        ("not-an-address", False),
        ("0x" + "g" * 40, False),   # non-hex
    ],
)
def test_is_valid_address(addr, ok):
    assert is_valid_address(addr) == ok


def test_load_seed_file_mixed_formats(tmp_path):
    content = """
wallets:
  - "0xAAAA000000000000000000000000000000000001"
  - address: "0xBBBB000000000000000000000000000000000002"
    name: "NamedWhale"
  - "invalid"
  - "0xBBBB000000000000000000000000000000000002"  # dup
"""
    p = tmp_path / "seeds.yaml"
    p.write_text(content)
    out = load_seed_file(p)
    assert out == [
        "0xaaaa000000000000000000000000000000000001",
        "0xbbbb000000000000000000000000000000000002",
    ]


def test_load_seed_file_missing(tmp_path):
    assert load_seed_file(tmp_path / "nope.yaml") == []
