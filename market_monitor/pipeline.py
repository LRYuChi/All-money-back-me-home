"""Daily Market Monitor Pipeline.

Fetches TW/US stock indicators + crypto status,
calculates technical signals, generates report.

Usage:
    python -m market_monitor.pipeline
"""

from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def fetch_market_data() -> dict:
    """Fetch all market data from free sources."""
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed")
        return {}

    data = {}

    # === US Indices ===
    us_tickers = {
        "^GSPC": "S&P 500",
        "^IXIC": "Nasdaq",
        "^DJI": "Dow Jones",
    }
    for ticker, name in us_tickers.items():
        try:
            df = yf.Ticker(ticker).history(period="6mo")
            if len(df) > 0:
                data[name] = _calc_indicators(df, name)
        except Exception as e:
            logger.warning("Failed %s: %s", ticker, e)

    # === US Stocks ===
    us_stocks = {"AAPL": "Apple", "NVDA": "NVIDIA", "TSM": "TSMC(US)", "MSFT": "Microsoft"}
    for ticker, name in us_stocks.items():
        try:
            df = yf.Ticker(ticker).history(period="6mo")
            if len(df) > 0:
                data[name] = _calc_indicators(df, name)
        except Exception as e:
            logger.warning("Failed %s: %s", ticker, e)

    # === Macro ===
    macro_tickers = {
        "^VIX": "VIX",
        "^TNX": "10Y殖利率",
        "GC=F": "黃金",
        "CL=F": "原油",
    }
    for ticker, name in macro_tickers.items():
        try:
            df = yf.Ticker(ticker).history(period="3mo")
            if len(df) > 0:
                close = df["Close"].iloc[-1]
                prev = df["Close"].iloc[-2] if len(df) > 1 else close
                chg = (close / prev - 1) * 100
                data[name] = {"price": close, "change_pct": chg, "name": name}
        except Exception as e:
            logger.warning("Failed %s: %s", ticker, e)

    # === Taiwan Index (yfinance for historical + TWSE OpenAPI for sector indices) ===
    try:
        df = yf.Ticker("^TWII").history(period="6mo")
        if len(df) > 0:
            data["加權指數"] = _calc_indicators(df, "加權指數")
    except Exception as e:
        logger.warning("TWII failed: %s", e)

    # === Taiwan Fundamentals (TWSE OpenAPI) ===
    try:
        from market_monitor.fetchers.twse_openapi import TWSEOpenAPIClient
        twse = TWSEOpenAPIClient()

        # Sector indices (semiconductor, electronics, finance)
        for idx in twse.get_sector_indices():
            name = idx["name"]
            if any(k in name for k in ["半導體", "電子", "金融保險"]):
                if idx["close"] is not None:
                    data[f"TW:{name}"] = {
                        "name": name,
                        "price": idx["close"],
                        "change_pct": idx["change_pct"] or 0,
                    }

        # Watchlist fundamentals
        watchlist_codes = ["2330", "2317", "2454", "2382"]
        fundamentals = twse.get_watchlist_fundamentals(watchlist_codes)
        if fundamentals:
            data["_tw_fundamentals"] = fundamentals

        # Institutional investors (三大法人)
        inst = twse.get_institutional_daily()
        if inst:
            data["_tw_institutional"] = inst
            # Per-stock institutional data for watchlist
            inst_stocks = twse.get_institutional_for_stocks(watchlist_codes)
            if inst_stocks:
                data["_tw_inst_stocks"] = inst_stocks
    except Exception as e:
        logger.warning("TWSE OpenAPI failed: %s", e)

    # === Crypto ===
    for ticker, name in [("BTC-USD", "BTC"), ("ETH-USD", "ETH"), ("SOL-USD", "SOL")]:
        try:
            df = yf.Ticker(ticker).history(period="3mo")
            if len(df) > 0:
                data[name] = _calc_indicators(df, name)
        except Exception as e:
            logger.warning("Failed %s: %s", ticker, e)

    return data


def _calc_indicators(df: pd.DataFrame, name: str) -> dict:
    """Calculate RSI, MA trend for a price series."""
    close = df["Close"]
    price = close.iloc[-1]
    prev = close.iloc[-2] if len(close) > 1 else price
    chg_pct = (price / prev - 1) * 100

    # RSI 14
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-10)
    rsi = (100 - 100 / (1 + rs)).iloc[-1]

    # MA200 trend
    ma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else np.nan
    above_ma200 = price > ma200 if not np.isnan(ma200) else None

    # MA50
    ma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else np.nan

    # Signals
    signals = []
    if rsi > 70:
        signals.append("RSI 超買")
    elif rsi < 30:
        signals.append("RSI 超賣")
    if above_ma200 is True:
        signals.append("MA200 ▲")
    elif above_ma200 is False:
        signals.append("MA200 ▼")

    return {
        "name": name,
        "price": price,
        "change_pct": chg_pct,
        "rsi": rsi,
        "ma50": ma50,
        "ma200": ma200,
        "above_ma200": above_ma200,
        "signals": signals,
    }


def generate_report(data: dict) -> str:
    """Generate formatted market report in Traditional Chinese."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = ["📊 *每日市場指標報告*", f"📅 {now}", ""]

    # US Indices
    lines.append("【美股指數】")
    for name in ["S&P 500", "Nasdaq", "Dow Jones"]:
        if name in data:
            d = data[name]
            trend = "▲" if d["above_ma200"] else "▼" if d["above_ma200"] is False else "—"
            lines.append(
                f"  {name}: {d['price']:,.0f} ({d['change_pct']:+.2f}%) "
                f"| RSI: {d['rsi']:.0f} | {trend}"
            )

    # Macro
    lines.append("")
    lines.append("【宏觀指標】")
    for name in ["VIX", "10Y殖利率", "黃金", "原油"]:
        if name in data:
            d = data[name]
            lines.append(f"  {name}: {d['price']:.2f} ({d['change_pct']:+.2f}%)")

    # Taiwan
    if "加權指數" in data:
        lines.append("")
        lines.append("【台股】")
        d = data["加權指數"]
        trend = "▲" if d["above_ma200"] else "▼" if d["above_ma200"] is False else "—"
        lines.append(
            f"  加權: {d['price']:,.0f} ({d['change_pct']:+.2f}%) "
            f"| RSI: {d['rsi']:.0f} | {trend}"
        )

    # TW Sector Indices (from TWSE OpenAPI)
    tw_sectors = {k: v for k, v in data.items() if k.startswith("TW:")}
    if tw_sectors:
        lines.append("")
        lines.append("【台股產業指數】")
        for key, d in tw_sectors.items():
            lines.append(
                f"  {d['name']}: {d['price']:,.2f} ({d['change_pct']:+.2f}%)"
            )

    # TW Watchlist Fundamentals (from TWSE OpenAPI)
    tw_fund = data.get("_tw_fundamentals", [])
    if tw_fund:
        lines.append("")
        lines.append("【台股基本面】")
        for f in tw_fund:
            pe = f"PE:{f['pe_ratio']:.1f}" if f.get("pe_ratio") else "PE:—"
            pb = f"PB:{f['pb_ratio']:.2f}" if f.get("pb_ratio") else "PB:—"
            dy = f"殖利率:{f['dividend_yield']:.2f}%" if f.get("dividend_yield") else "殖利率:—"
            lines.append(f"  {f['name']}({f['code']}): {pe} | {pb} | {dy}")

    # TW Institutional Investors (三大法人)
    tw_inst = data.get("_tw_institutional", {})
    if tw_inst:
        lines.append("")
        lines.append("【三大法人買賣超】")
        foreign = tw_inst.get("foreign", {})
        trust = tw_inst.get("trust", {})
        dealer = tw_inst.get("dealer", {})
        total = tw_inst.get("total", {})

        def _sign_yi(v: float) -> str:
            """Format 億元 with sign."""
            return f"+{v:,.2f}" if v > 0 else f"{v:,.2f}"

        def _sign_lots(v: float) -> str:
            """Format 張 with sign."""
            return f"+{v:,.0f}" if v > 0 else f"{v:,.0f}"

        if foreign:
            lines.append(
                f"  外資: {_sign_yi(foreign.get('net', 0))} 億"
                f" (買 {foreign.get('buy', 0):,.2f} / 賣 {foreign.get('sell', 0):,.2f})"
            )
        if trust:
            lines.append(
                f"  投信: {_sign_yi(trust.get('net', 0))} 億"
                f" (買 {trust.get('buy', 0):,.2f} / 賣 {trust.get('sell', 0):,.2f})"
            )
        if dealer:
            lines.append(
                f"  自營: {_sign_yi(dealer.get('net', 0))} 億"
                f" (買 {dealer.get('buy', 0):,.2f} / 賣 {dealer.get('sell', 0):,.2f})"
            )
        if total:
            lines.append(f"  合計: {_sign_yi(total.get('net', 0))} 億")

        # Watchlist institutional detail (per-stock, in 張)
        inst_stocks = data.get("_tw_inst_stocks", [])
        if inst_stocks:
            lines.append("")
            lines.append("【觀察清單籌碼】")
            for s in inst_stocks:
                fn = s.get("foreign_net", 0)
                tn = s.get("trust_net", 0)
                # Display in 張 (lots of 1000 shares)
                fn_lots = fn / 1000
                tn_lots = tn / 1000
                emoji = "🟢" if fn > 0 and tn > 0 else "🔴" if fn < 0 and tn < 0 else "🟡"
                lines.append(
                    f"  {emoji} {s['name']}({s['code']}): "
                    f"外資 {_sign_lots(fn_lots)} 張 | 投信 {_sign_lots(tn_lots)} 張"
                )

    # US Stocks
    lines.append("")
    lines.append("【個股】")
    for name in ["Apple", "NVIDIA", "TSMC(US)", "Microsoft"]:
        if name in data:
            d = data[name]
            signals = " | ".join(d["signals"]) if d["signals"] else ""
            lines.append(
                f"  {name}: {d['price']:.2f} ({d['change_pct']:+.2f}%) "
                f"| RSI: {d['rsi']:.0f} {signals}"
            )

    # Crypto
    lines.append("")
    lines.append("【加密貨幣】")
    for name in ["BTC", "ETH", "SOL"]:
        if name in data:
            d = data[name]
            lines.append(
                f"  {name}: ${d['price']:,.0f} ({d['change_pct']:+.2f}%) "
                f"| RSI: {d['rsi']:.0f}"
            )

    # Alerts
    alerts = []
    for name, d in data.items():
        if "signals" in d:
            for sig in d["signals"]:
                if "超買" in sig or "超賣" in sig:
                    alerts.append(f"  ⚠️ {name}: {sig}")

    if alerts:
        lines.append("")
        lines.append("【警示信號】")
        lines.extend(alerts)

    return "\n".join(lines)


def main():
    """Run pipeline and send report."""
    print("Fetching market data...")
    data = fetch_market_data()

    if not data:
        print("No data fetched")
        return

    report = generate_report(data)
    print(report)

    # Send to Telegram
    try:
        from market_monitor.telegram_zh import send_message
        send_message(report)
        print("\nReport sent to Telegram")
    except Exception as e:
        print(f"\nTelegram send failed: {e}")


if __name__ == "__main__":
    main()
