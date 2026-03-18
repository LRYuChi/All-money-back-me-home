"""Cross-Market Correlation Analysis.

Tracks rolling correlations between BTC and traditional markets
to detect regime changes (convergence/divergence).

Usage:
    python -m market_monitor.correlation
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def fetch_correlation_data(period: str = "6mo") -> pd.DataFrame | None:
    """Fetch daily returns for correlation analysis."""
    try:
        import yfinance as yf
    except ImportError:
        return None

    tickers = {
        "BTC": "BTC-USD",
        "SPY": "SPY",
        "Gold": "GC=F",
        "Oil": "CL=F",
        "TWII": "^TWII",
    }

    frames = {}
    for name, ticker in tickers.items():
        try:
            df = yf.Ticker(ticker).history(period=period)
            if len(df) > 0:
                # Normalize timezone-aware index to date only
                closes = df["Close"].copy()
                closes.index = closes.index.normalize()
                frames[name] = closes.pct_change().dropna()
        except Exception:
            pass

    if len(frames) < 2 or "BTC" not in frames:
        return None

    # Merge on date, keeping all BTC dates
    combined = pd.DataFrame(frames).ffill().dropna(thresh=2)
    return combined


def rolling_correlation(returns: pd.DataFrame, target: str = "BTC",
                        window: int = 30) -> pd.DataFrame:
    """Calculate rolling correlation of all assets vs target."""
    if target not in returns.columns:
        return pd.DataFrame()

    corrs = {}
    for col in returns.columns:
        if col != target:
            corrs[f"{target}-{col}"] = returns[target].rolling(window).corr(returns[col])

    return pd.DataFrame(corrs).dropna()


def generate_correlation_report(returns: pd.DataFrame) -> str:
    """Generate correlation analysis report."""
    if returns is None or returns.empty:
        return "無法取得相關性數據"

    # Current correlations (30-day rolling)
    corr_30d = rolling_correlation(returns, "BTC", 30)

    # Short-term correlations (7-day)
    corr_7d = rolling_correlation(returns, "BTC", 7)

    lines = ["📊 *跨市場相關性分析*", "(BTC vs 傳統市場 — 30日滾動)", ""]

    if corr_30d.empty:
        return "相關性數據不足"

    latest_30d = corr_30d.iloc[-1]
    latest_7d = corr_7d.iloc[-1] if not corr_7d.empty else latest_30d

    for pair in latest_30d.index:
        val_30 = latest_30d[pair]
        val_7 = latest_7d.get(pair, val_30)
        asset = pair.replace("BTC-", "")

        # Trend arrow
        if val_7 > val_30 + 0.1:
            trend = "↑ 增強"
        elif val_7 < val_30 - 0.1:
            trend = "↓ 減弱"
        else:
            trend = "→ 穩定"

        # Interpretation
        if abs(val_30) > 0.7:
            strength = "強"
        elif abs(val_30) > 0.4:
            strength = "中"
        else:
            strength = "弱"

        direction = "正" if val_30 > 0 else "負"

        lines.append(f"  BTC-{asset}: {val_30:+.2f} ({strength}{direction}相關) {trend}")

    # Regime assessment
    spy_corr = latest_30d.get("BTC-SPY", 0)
    gold_corr = latest_30d.get("BTC-Gold", 0)

    lines.append("")
    lines.append("【狀態判斷】")

    if spy_corr > 0.6:
        lines.append("  ⚠️ BTC 高度跟隨美股 — 獨立性低，注意系統性風險")
    elif spy_corr < -0.2:
        lines.append("  ✅ BTC 與美股脫鉤 — 獨立行情，Alpha 機會")
    else:
        lines.append("  → BTC-SPY 低相關 — 正常獨立運行")

    if gold_corr > 0.4:
        lines.append("  🥇 BTC 表現為「數位黃金」— 避險需求驅動")
    elif gold_corr < -0.3:
        lines.append("  📈 BTC 表現為風險資產 — 與黃金反向")

    return "\n".join(lines)


def main():
    print("Fetching cross-market data...")
    returns = fetch_correlation_data()
    if returns is not None:
        report = generate_correlation_report(returns)
        print(report)

        try:
            from market_monitor.telegram_zh import send_message
            send_message(report)
            print("\nReport sent to Telegram")
        except Exception as e:
            print(f"\nTelegram: {e}")
    else:
        print("Failed to fetch data")


if __name__ == "__main__":
    main()
