"""
Run Backtest — 回測 BB Squeeze 策略
Usage: cd apps/api && python -m src.jobs.run_backtest [--symbol BTC/USDT] [--timeframe 1h] [--days 180] [--walk-forward]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add src/ to path
_SRC_DIR = str(Path(__file__).resolve().parents[1])
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import ccxt
import pandas as pd

from strategy.layer3_strategies.strategy_b_squeeze import BBSqueezeStrategy
from strategy.layer5_backtest import (
    BacktestConfig,
    BacktestEngine,
    WalkForwardConfig,
    WalkForwardRunner,
)


def fetch_historical(
    symbol: str,
    timeframe: str,
    days: int,
) -> pd.DataFrame:
    """Fetch historical OHLCV data from Binance (public, no key needed)."""
    exchange = ccxt.binance({"enableRateLimit": True})
    since_ms = exchange.parse8601(
        (datetime.now(timezone.utc).__class__(
            datetime.now(timezone.utc).year,
            datetime.now(timezone.utc).month,
            datetime.now(timezone.utc).day,
            tzinfo=timezone.utc,
        ) - pd.Timedelta(days=days)).isoformat()
    )

    all_ohlcv = []
    limit = 1000

    print(f"正在下載 {symbol} {timeframe} 歷史數據 ({days} 天)...")

    while True:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=limit)
        if not ohlcv:
            break
        all_ohlcv.extend(ohlcv)
        since_ms = ohlcv[-1][0] + 1  # Next ms after last candle
        if len(ohlcv) < limit:
            break

    df = pd.DataFrame(
        all_ohlcv, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"]
    )
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], unit="ms", utc=True)
    df.set_index("Timestamp", inplace=True)
    df = df[~df.index.duplicated(keep="first")]

    print(f"已下載 {len(df)} 根K線 ({df.index[0].date()} ~ {df.index[-1].date()})")
    return df


def print_result(result, title: str = "回測結果") -> None:
    """Pretty-print backtest results in Traditional Chinese."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")
    print(f"  標的: {result.symbol}")
    print(f"  週期: {result.period}")
    print(f"  總交易數: {result.total_trades}")
    print(f"  勝率: {result.win_rate:.1%}")
    print(f"  盈虧比 (Profit Factor): {result.profit_factor:.2f}")
    print(f"  總報酬: {result.total_return:.2%}")
    print(f"  最大回撤: {result.max_drawdown:.2%}")
    print(f"  Sharpe Ratio: {result.sharpe_ratio:.2f}")
    print(f"  Calmar Ratio: {result.calmar_ratio:.2f}")
    print(f"  平均持倉 (bars): {result.avg_trade_duration_bars:.1f}")
    print(f"  平均 R-Multiple: {result.avg_r_multiple:.2f}")

    if result.trades:
        wins = [t for t in result.trades if t["pnl_usd"] > 0]
        losses = [t for t in result.trades if t["pnl_usd"] <= 0]
        print("\n  --- 交易明細 ---")
        print(f"  獲利筆數: {len(wins)} | 虧損筆數: {len(losses)}")
        if wins:
            avg_win = sum(t["pnl_pct"] for t in wins) / len(wins)
            print(f"  平均獲利: {avg_win:.2f}%")
        if losses:
            avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses)
            print(f"  平均虧損: {avg_loss:.2f}%")

    print(f"{'=' * 60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="BB Squeeze 策略回測")
    parser.add_argument("--symbol", default="BTC/USDT", help="交易對 (default: BTC/USDT)")
    parser.add_argument("--timeframe", default="1h", help="K線週期 (default: 1h)")
    parser.add_argument("--days", type=int, default=180, help="回測天數 (default: 180)")
    parser.add_argument("--capital", type=float, default=10000.0, help="初始資金 (default: 10000)")
    parser.add_argument("--walk-forward", action="store_true", help="啟用 Walk-Forward 驗證")
    parser.add_argument("--save", action="store_true", help="儲存結果至 data/backtest/")
    args = parser.parse_args()

    # Fetch data
    df = fetch_historical(args.symbol, args.timeframe, args.days)

    config = BacktestConfig(
        symbol=args.symbol,
        timeframe=args.timeframe,
        initial_capital=args.capital,
    )

    if args.walk_forward:
        print("\n啟動 Walk-Forward 驗證...")
        runner = WalkForwardRunner(
            strategy_factory=lambda: BBSqueezeStrategy(),
            backtest_config=config,
            wf_config=WalkForwardConfig(n_splits=5),
        )
        folds = runner.run(df)
        summary = WalkForwardRunner.summarize(folds)

        print(f"\n{'=' * 60}")
        print("  Walk-Forward 驗證結果")
        print(f"{'=' * 60}")
        print(f"  Folds: {summary['n_folds']}")
        print(f"  平均勝率 (OOS): {summary['avg_win_rate']:.1%}")
        print(f"  平均 Sharpe (OOS): {summary['avg_sharpe']:.2f}")
        print(f"  平均最大回撤 (OOS): {summary['avg_max_drawdown']:.2%}")
        print(f"  平均報酬 (OOS): {summary['avg_return']:.2%}")
        print(f"  總交易數 (OOS): {summary['total_trades']}")
        print(f"{'=' * 60}\n")

        for fold in folds:
            if fold.test_result:
                print_result(
                    fold.test_result,
                    title=f"Fold {fold.fold_index} — Out-of-Sample",
                )

        if args.save:
            _save_results({"walk_forward": summary}, args)
            _save_to_supabase_wf(summary, config)
    else:
        # Single backtest
        engine = BacktestEngine(strategy=BBSqueezeStrategy(), config=config)
        result = engine.run(df)
        print_result(result)

        if args.save:
            _save_results(result.model_dump(mode="json"), args)
            _save_to_supabase(result, config)


def _save_results(data: dict, args) -> None:
    """Save backtest results to JSON."""
    try:
        _fallback = Path(__file__).resolve().parents[4] / "data"
    except IndexError:
        _fallback = Path(__file__).resolve().parent.parent.parent / "data"
    data_dir = Path(os.environ["DATA_DIR"]) if "DATA_DIR" in os.environ else _fallback
    out_dir = data_dir / "backtest"
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    symbol_safe = args.symbol.replace("/", "")
    filename = f"{symbol_safe}_{args.timeframe}_{ts}.json"

    filepath = out_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    print(f"結果已儲存至 {filepath}")


def _save_to_supabase(result, config: BacktestConfig) -> None:
    """Save backtest result to Supabase."""
    try:
        from src.services.trade_store import TradeStore
        store = TradeStore()
        if store.has_supabase:
            run_id = store.save_backtest_run(
                result.model_dump(mode="json"),
                config.model_dump(),
            )
            if run_id:
                print(f"回測結果已儲存至 Supabase (ID: {run_id})")
    except Exception as e:
        print(f"[Supabase] 儲存回測結果失敗: {e}")


def _save_to_supabase_wf(summary: dict, config: BacktestConfig) -> None:
    """Save walk-forward result to Supabase."""
    try:
        from src.services.trade_store import TradeStore
        store = TradeStore()
        if store.has_supabase:
            run_id = store.save_walk_forward_run(
                summary,
                {
                    "strategy": "B_SQUEEZE",
                    "symbol": config.symbol,
                    "timeframe": config.timeframe,
                    "initial_capital": config.initial_capital,
                    "commission_rate": config.commission_rate,
                },
            )
            if run_id:
                print(f"Walk-Forward 結果已儲存至 Supabase (ID: {run_id})")
    except Exception as e:
        print(f"[Supabase] 儲存 WF 結果失敗: {e}")


if __name__ == "__main__":
    main()
