"""Integration tests for PostgresStore.

這些測試需要真實 Supabase/Postgres 連線(DATABASE_URL 設定).
沒有 DATABASE_URL 時整組 skip,不影響 CI.

測試原則:
- 用隨機 test prefix 地址,不會干擾 production data
- 每個 test tearDown 時自動清掉自己建的 wallet + trades(cascade)
- 不依賴測試順序
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from dotenv import load_dotenv

from smart_money.store.schema import Ranking, Trade

# Load .env before reading env vars
load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — skipping PostgresStore integration tests.",
)


@pytest.fixture
def store():
    from smart_money.store.db import PostgresStore
    return PostgresStore(DATABASE_URL)


@pytest.fixture
def test_address():
    """Random EVM-like address in the test-only prefix range."""
    # Use 0xdeadbeef...<random-hex> so we can't clash with real HL addresses
    tail = uuid.uuid4().hex[:32]
    return f"0xdeadbeef{tail}"


@pytest.fixture
def cleanup(store, test_address):
    """Teardown: delete everything tied to our test wallet."""
    yield
    import psycopg
    with psycopg.connect(DATABASE_URL, prepare_threshold=None) as conn, conn.cursor() as cur:
        cur.execute("delete from sm_wallets where address = %s", (test_address,))
        conn.commit()


@pytest.fixture
def now_utc():
    return datetime.now(tz=timezone.utc).replace(microsecond=0)


def _make_trade(wallet_id, tid, ts, pnl=None, size=1.0, action="close"):
    return Trade(
        wallet_id=wallet_id,
        hl_trade_id=str(tid),
        symbol="BTC",
        side="long",
        action=action,
        size=size,
        price=50000.0,
        pnl=pnl,
        fee=0.05,
        ts=ts,
    )


# ------------------------------------------------------------------ #
def test_connection_smoke(store):
    """Trivial: can we run a query?"""
    import psycopg
    with psycopg.connect(DATABASE_URL, prepare_threshold=None) as conn, conn.cursor() as cur:
        cur.execute("select 1")
        assert cur.fetchone()[0] == 1


def test_upsert_wallet_creates_and_returns(store, test_address, now_utc, cleanup):
    w1 = store.upsert_wallet(test_address, seen_at=now_utc)
    assert w1.address == test_address
    assert w1.first_seen_at == now_utc
    assert w1.last_active_at == now_utc

    w2 = store.upsert_wallet(test_address, seen_at=now_utc + timedelta(hours=1))
    assert w2.id == w1.id
    assert w2.last_active_at == now_utc + timedelta(hours=1)

    # get_wallet_by_address 應拿到一樣的 id
    got = store.get_wallet_by_address(test_address)
    assert got is not None and got.id == w1.id


def test_upsert_trades_idempotent(store, test_address, now_utc, cleanup):
    w = store.upsert_wallet(test_address, seen_at=now_utc)
    trades = [
        _make_trade(w.id, i, now_utc + timedelta(minutes=i), pnl=float(i))
        for i in range(5)
    ]
    n1 = store.upsert_trades(trades)
    assert n1 == 5
    # 重跑同一批 → 0 新增
    store.upsert_trades(trades)
    assert store.count_trades(w.id) == 5


def test_get_trades_time_filter(store, test_address, now_utc, cleanup):
    w = store.upsert_wallet(test_address, seen_at=now_utc)
    trades = [
        _make_trade(w.id, i, now_utc + timedelta(hours=i), pnl=float(i))
        for i in range(10)
    ]
    store.upsert_trades(trades)

    since = now_utc + timedelta(hours=5)
    filtered = store.get_trades(w.id, since=since)
    assert len(filtered) == 5
    assert all(t.ts >= since for t in filtered)


def test_get_last_trade_ts(store, test_address, now_utc, cleanup):
    w = store.upsert_wallet(test_address, seen_at=now_utc)
    trades = [
        _make_trade(w.id, i, now_utc + timedelta(minutes=i * 10), pnl=float(i))
        for i in range(3)
    ]
    store.upsert_trades(trades)
    last = store.get_last_trade_ts(w.id)
    assert last == now_utc + timedelta(minutes=20)


def test_add_tag_idempotent(store, test_address, now_utc, cleanup):
    w = store.upsert_wallet(test_address, seen_at=now_utc)
    store.add_tag(w.id, "test_tag")
    store.add_tag(w.id, "test_tag")        # idempotent
    store.add_tag(w.id, "another_tag")

    got = store.get_wallet_by_address(test_address)
    assert "test_tag" in got.tags
    assert "another_tag" in got.tags
    assert got.tags.count("test_tag") == 1


def test_list_wallets_filter_by_tag(store, test_address, now_utc, cleanup):
    w = store.upsert_wallet(test_address, seen_at=now_utc)
    unique_tag = f"test_{uuid.uuid4().hex[:8]}"
    store.add_tag(w.id, unique_tag)

    tagged = store.list_wallets(tag=unique_tag)
    ids = [x.id for x in tagged]
    assert w.id in ids


def test_save_ranking(store, test_address, now_utc, cleanup):
    w = store.upsert_wallet(test_address, seen_at=now_utc)
    snapshot = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)
    ranking = Ranking(
        snapshot_date=snapshot,
        wallet_id=w.id,
        rank=1,
        score=0.85,
        metrics={"sortino": 2.3, "profit_factor": 1.8, "test_marker": test_address},
    )
    n = store.save_ranking([ranking])
    assert n == 1

    # Verify persisted
    import psycopg
    import json
    with psycopg.connect(DATABASE_URL, prepare_threshold=None) as conn, conn.cursor() as cur:
        cur.execute(
            "select rank, score, metrics from sm_rankings "
            "where wallet_id = %s and snapshot_date = %s",
            (str(w.id), snapshot.date()),
        )
        row = cur.fetchone()
        assert row is not None
        rank, score, metrics = row
        assert rank == 1
        assert float(score) == pytest.approx(0.85)
        metrics_dict = metrics if isinstance(metrics, dict) else json.loads(metrics)
        assert metrics_dict["test_marker"] == test_address


def test_save_ranking_upsert(store, test_address, now_utc, cleanup):
    """同 (snapshot_date, wallet_id) 第二次寫應更新,而非 duplicate."""
    w = store.upsert_wallet(test_address, seen_at=now_utc)
    snapshot = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)

    store.save_ranking([Ranking(snapshot, w.id, rank=1, score=0.5, metrics={"v": 1})])
    store.save_ranking([Ranking(snapshot, w.id, rank=2, score=0.9, metrics={"v": 2})])

    import psycopg
    with psycopg.connect(DATABASE_URL, prepare_threshold=None) as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*), max(score) from sm_rankings "
            "where wallet_id = %s and snapshot_date = %s",
            (str(w.id), snapshot.date()),
        )
        count, max_score = cur.fetchone()
        assert count == 1
        assert float(max_score) == pytest.approx(0.9)


def test_factory_picks_postgres_when_database_url_set(store, monkeypatch):
    """build_store 應優先選 PostgresStore."""
    from smart_money.store.db import PostgresStore, build_store

    class FakeSettings:
        database_url = DATABASE_URL
        supabase_url = ""
        supabase_service_key = ""

    s = build_store(FakeSettings())
    assert isinstance(s, PostgresStore)
