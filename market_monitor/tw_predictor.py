"""台股大盤預測器 — 多空環境 + 籌碼 + 技術分析。

整合三引擎產出綜合評分 + AI 分析報告，推送至 Telegram。

使用方式：
    from market_monitor.tw_predictor import predict, format_report
    result = predict()
    report = format_report(result)
"""

from __future__ import annotations

import logging
import os
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Taiwan timezone
TW_TZ = timezone(timedelta(hours=8))


# ============================================================
# PURE NUMPY TA FUNCTIONS (no talib dependency)
# ============================================================

def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    atr = pd.Series(tr).rolling(period).mean().values
    return atr

def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(period).mean().values
    avg_loss = pd.Series(loss).rolling(period).mean().values
    rs = avg_gain / np.where(avg_loss == 0, 1e-10, avg_loss)
    return 100 - (100 / (1 + rs))

def _macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = pd.Series(close).ewm(span=fast).mean().values
    ema_slow = pd.Series(close).ewm(span=slow).mean().values
    macd_line = ema_fast - ema_slow
    signal_line = pd.Series(macd_line).ewm(span=signal).mean().values
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    up_move = high - np.roll(high, 1)
    down_move = np.roll(low, 1) - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    atr_vals = _atr(high, low, close, period)
    plus_di = 100 * pd.Series(plus_dm).rolling(period).mean().values / np.where(atr_vals == 0, 1e-10, atr_vals)
    minus_di = 100 * pd.Series(minus_dm).rolling(period).mean().values / np.where(atr_vals == 0, 1e-10, atr_vals)
    dx = 100 * np.abs(plus_di - minus_di) / np.where((plus_di + minus_di) == 0, 1e-10, plus_di + minus_di)
    return pd.Series(dx).rolling(period).mean().values

def _bbands(close: np.ndarray, period: int = 20, nbdev: float = 2.0):
    middle = pd.Series(close).rolling(period).mean().values
    std = pd.Series(close).rolling(period).std().values
    upper = middle + nbdev * std
    lower = middle - nbdev * std
    return upper, middle, lower

# Default watchlist
TW_WATCHLIST = ["2330", "2317", "2454", "2382", "2881", "2891", "3711", "6547"]


# ============================================================
# DATA FETCHERS
# ============================================================

def _fetch_yf(ticker: str, period: str = "6mo") -> pd.DataFrame | None:
    """Fetch OHLCV from yfinance with error handling."""
    try:
        import yfinance as yf
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if df is not None and len(df) > 0:
            df.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df.columns]
            return df
    except Exception as e:
        logger.warning("yfinance fetch %s failed: %s", ticker, e)
    return None


def _fetch_twse_institutional() -> dict:
    """Fetch 三大法人買賣超 from TWSE OpenAPI."""
    try:
        from market_monitor.fetchers.twse_openapi import _fetch_endpoint
        data = _fetch_endpoint("exchangeReport/FINI_T")
        if data:
            result = {}
            for row in data:
                name = row.get("名稱", "")
                buy = float(row.get("買進金額", "0").replace(",", "")) / 1e8  # 億
                sell = float(row.get("賣出金額", "0").replace(",", "")) / 1e8
                net = buy - sell
                if "外資" in name and "自營" not in name:
                    result["foreign"] = round(net, 1)
                elif "投信" in name:
                    result["investment_trust"] = round(net, 1)
                elif "自營商" in name and "合計" in name:
                    result["dealers"] = round(net, 1)
            return result
    except Exception as e:
        logger.warning("TWSE institutional fetch failed: %s", e)
    return {}


def _fetch_twse_margin() -> dict:
    """Fetch 融資融券 from TWSE OpenAPI."""
    try:
        from market_monitor.fetchers.twse_openapi import _fetch_endpoint
        data = _fetch_endpoint("exchangeReport/MI_MARGN")
        if data:
            # Last row is the total
            for row in data:
                if row.get("股票代號", "") == "" or "合計" in row.get("股票名稱", ""):
                    margin_buy = float(row.get("融資買進", "0").replace(",", ""))
                    margin_sell = float(row.get("融資賣出", "0").replace(",", ""))
                    margin_balance = float(row.get("融資餘額", "0").replace(",", ""))
                    short_sell = float(row.get("融券賣出", "0").replace(",", ""))
                    short_buy = float(row.get("融券買進", "0").replace(",", ""))
                    short_balance = float(row.get("融券餘額", "0").replace(",", ""))
                    return {
                        "margin_net": round(margin_buy - margin_sell, 0),
                        "margin_balance": round(margin_balance, 0),
                        "short_net": round(short_sell - short_buy, 0),
                        "short_balance": round(short_balance, 0),
                    }
    except Exception as e:
        logger.warning("TWSE margin fetch failed: %s", e)
    return {}


# ============================================================
# MODULE 1: 多空環境引擎
# ============================================================

def calc_direction_score() -> dict:
    """Calculate TAIEX direction score [-100, +100]."""
    # Using built-in TA functions (no talib dependency)

    taiex = _fetch_yf("^TWII", period="1y")
    if taiex is None or len(taiex) < 60:
        return {"score": 0, "factors": {}, "error": "TAIEX data unavailable"}

    close = taiex["close"].values.flatten()
    factors = {}

    # 1. MA alignment (25%)
    ma20 = close[-1] > np.mean(close[-20:]) if len(close) >= 20 else None
    ma60 = close[-1] > np.mean(close[-60:]) if len(close) >= 60 else None
    ma120 = close[-1] > np.mean(close[-120:]) if len(close) >= 120 else None
    ma_count = sum(x for x in [ma20, ma60, ma120] if x is not None)
    ma_total = sum(1 for x in [ma20, ma60, ma120] if x is not None)
    ma_score = ((ma_count / ma_total) * 2 - 1) * 100 if ma_total > 0 else 0
    factors["ma_alignment"] = {"score": round(ma_score), "ma20": ma20, "ma60": ma60, "ma120": ma120}

    # 2. Supertrend direction (15%)
    try:
        atr = _atr(taiex["high"].values.flatten(), taiex["low"].values.flatten(), close, period=10)
        src = (taiex["high"].values.flatten() + taiex["low"].values.flatten()) / 2
        # Simple Supertrend direction check
        up = src[-1] - 3.0 * atr[-1] if not np.isnan(atr[-1]) else 0
        dn = src[-1] + 3.0 * atr[-1] if not np.isnan(atr[-1]) else 0
        st_bull = close[-1] > dn
        st_score = 100 if st_bull else -100
    except Exception:
        st_score = 0
    factors["supertrend"] = {"score": st_score, "direction": "多" if st_score > 0 else "空"}

    # 3. International correlation (15%)
    sp500 = _fetch_yf("^GSPC", period="1mo")
    sox = _fetch_yf("^SOX", period="1mo")
    intl_score = 0
    intl_detail = {}
    if sp500 is not None and len(sp500) > 5:
        sp_chg = (sp500["close"].iloc[-1] / sp500["close"].iloc[-5] - 1) * 100
        intl_score += 50 if float(sp_chg) > 0 else -50
        intl_detail["sp500_5d"] = f"{float(sp_chg):+.1f}%"
    if sox is not None and len(sox) > 5:
        sox_chg = (sox["close"].iloc[-1] / sox["close"].iloc[-5] - 1) * 100
        intl_score += 50 if float(sox_chg) > 0 else -50
        intl_detail["sox_5d"] = f"{float(sox_chg):+.1f}%"
    factors["international"] = {"score": intl_score, **intl_detail}

    # 4. VIX (10%)
    vix = _fetch_yf("^VIX", period="1mo")
    vix_score = 0
    if vix is not None and len(vix) > 0:
        vix_val = float(vix["close"].iloc[-1])
        vix_score = 100 if vix_val < 15 else (50 if vix_val < 20 else (0 if vix_val < 25 else (-50 if vix_val < 35 else -100)))
        factors["vix"] = {"score": vix_score, "value": round(vix_val, 1)}
    else:
        factors["vix"] = {"score": 0, "value": None}

    # 5. TWD (10%)
    twd = _fetch_yf("TWD=X", period="1mo")
    twd_score = 0
    if twd is not None and len(twd) > 5:
        twd_now = float(twd["close"].iloc[-1])
        twd_5d = float(twd["close"].iloc[-5])
        twd_chg = (twd_now / twd_5d - 1) * 100
        # TWD strengthening (lower number) = bullish for TAIEX
        twd_score = 50 if twd_chg < -0.3 else (-50 if twd_chg > 0.3 else 0)
        factors["twd"] = {"score": twd_score, "rate": round(twd_now, 2), "change_5d": f"{twd_chg:+.2f}%"}
    else:
        factors["twd"] = {"score": 0}

    # 6. Volume trend (10%)
    if len(taiex) > 20 and "volume" in taiex.columns:
        vol = taiex["volume"].values.flatten()
        vol_ratio = float(vol[-1]) / float(np.mean(vol[-20:])) if np.mean(vol[-20:]) > 0 else 1
        vol_score = 30 if vol_ratio > 1.2 else (-30 if vol_ratio < 0.7 else 0)
        factors["volume"] = {"score": vol_score, "ratio": round(vol_ratio, 2)}
    else:
        factors["volume"] = {"score": 0}

    # Weighted composite
    composite = (
        ma_score * 0.25
        + st_score * 0.15
        + intl_score * 0.15
        + vix_score * 0.10
        + twd_score * 0.10
        + factors.get("volume", {}).get("score", 0) * 0.10
    )
    # Remaining 15% reserved for seasonality (not implemented yet)

    return {"score": round(composite), "factors": factors, "taiex": round(float(close[-1]), 1)}


# ============================================================
# MODULE 2: 籌碼引擎
# ============================================================

def calc_institutional_score() -> dict:
    """Calculate institutional flow score [-100, +100]."""
    inst = _fetch_twse_institutional()
    margin = _fetch_twse_margin()

    score = 0
    factors = {}

    # Foreign investors
    foreign = inst.get("foreign", 0)
    if foreign > 50:
        score += 40
    elif foreign > 0:
        score += 20
    elif foreign > -50:
        score -= 20
    else:
        score -= 40
    factors["foreign_net"] = f"{foreign:+.1f} 億"

    # Investment trust
    trust = inst.get("investment_trust", 0)
    if trust > 10:
        score += 20
    elif trust > 0:
        score += 10
    elif trust < -10:
        score -= 20
    else:
        score -= 10
    factors["trust_net"] = f"{trust:+.1f} 億"

    # Dealers
    dealers = inst.get("dealers", 0)
    factors["dealers_net"] = f"{dealers:+.1f} 億"

    # Margin (contrarian)
    margin_net = margin.get("margin_net", 0)
    if margin_net > 5000:
        score -= 10  # Retail chasing = bearish signal
    elif margin_net < -3000:
        score += 10  # Retail selling = contrarian bullish
    factors["margin_net"] = f"{margin_net:+.0f} 張"
    factors["margin_balance"] = margin.get("margin_balance", 0)

    # === TAIFEX 期權籌碼 ===
    derivatives = {}
    try:
        from market_monitor.fetchers.taifex import get_derivatives_summary
        deriv = get_derivatives_summary()
        derivatives = deriv

        # Add derivatives score
        score += deriv.get("score", 0)

        # PC Ratio
        pc = deriv.get("pc_ratio", {})
        if "error" not in pc:
            factors["pc_ratio_oi"] = f"{pc.get('oi_pc_ratio', 0):.1f}%"
            factors["pc_ratio_vol"] = f"{pc.get('volume_pc_ratio', 0):.1f}%"
            factors["put_oi"] = f"{pc.get('put_oi', 0):,}"
            factors["call_oi"] = f"{pc.get('call_oi', 0):,}"

        # Futures institutional
        fut = deriv.get("futures", {})
        if "error" not in fut:
            factors["futures_foreign_net"] = f"{fut.get('foreign_net', 0):+,} 口"

        factors["derivatives_signals"] = deriv.get("signals", [])

        # Retail futures
        retail = deriv.get("retail", {})
        if "error" not in retail and retail:
            factors["retail_net"] = f"{retail.get('retail_net', 0):+,} 口"
            factors["retail_sentiment"] = retail.get("sentiment", "")

        # Options OI distribution
        opt_oi = deriv.get("options_oi", {})
        if "error" not in opt_oi and opt_oi:
            factors["options_expiry"] = opt_oi.get("expiry", "")
            factors["max_call_strike"] = f"{opt_oi.get('max_call_strike', 0):,}"
            factors["max_call_oi"] = f"{opt_oi.get('max_call_oi', 0):,}"
            factors["max_put_strike"] = f"{opt_oi.get('max_put_strike', 0):,}"
            factors["max_put_oi"] = f"{opt_oi.get('max_put_oi', 0):,}"
            factors["top5_call"] = opt_oi.get("top5_call", [])
            factors["top5_put"] = opt_oi.get("top5_put", [])

    except Exception as e:
        logger.warning("TAIFEX derivatives fetch failed: %s", e)

    return {"score": max(-100, min(100, score)), "factors": factors,
            "raw": {**inst, **margin}, "derivatives": derivatives}


# ============================================================
# MODULE 3: 技術分析引擎
# ============================================================

def calc_technical_score(ticker: str = "^TWII") -> dict:
    """Calculate technical analysis score for a ticker."""
    # Using built-in TA functions (no talib dependency)

    df = _fetch_yf(ticker, period="6mo")
    if df is None or len(df) < 30:
        return {"score": 0, "factors": {}, "error": "Data unavailable"}

    close = df["close"].values.flatten()
    high = df["high"].values.flatten()
    low = df["low"].values.flatten()

    factors = {}
    score = 0

    # RSI
    rsi = _rsi(close, period=14)
    rsi_val = float(rsi[-1]) if not np.isnan(rsi[-1]) else 50
    if rsi_val < 30:
        rsi_score = 50  # Oversold = bullish
    elif rsi_val < 40:
        rsi_score = 20
    elif rsi_val > 70:
        rsi_score = -50  # Overbought = bearish
    elif rsi_val > 60:
        rsi_score = -20
    else:
        rsi_score = 0
    score += rsi_score * 0.25
    factors["rsi"] = {"value": round(rsi_val, 1), "signal": "超賣" if rsi_val < 30 else ("超買" if rsi_val > 70 else "中性")}

    # MACD
    macd, signal, hist = _macd(close)
    if not np.isnan(hist[-1]):
        macd_bull = float(hist[-1]) > 0 and float(hist[-1]) > float(hist[-2]) if not np.isnan(hist[-2]) else False
        macd_score = 50 if macd_bull else -50
    else:
        macd_score = 0
    score += macd_score * 0.25
    factors["macd"] = {"signal": "多頭" if macd_score > 0 else "空頭", "histogram": round(float(hist[-1]), 2) if not np.isnan(hist[-1]) else 0}

    # ADX
    adx = _adx(high, low, close, period=14)
    adx_val = float(adx[-1]) if not np.isnan(adx[-1]) else 20
    factors["adx"] = {"value": round(adx_val, 1), "trend": "強" if adx_val > 25 else "弱"}

    # Bollinger Bands position
    upper, middle, lower = _bbands(close, period=20)
    if not np.isnan(upper[-1]) and not np.isnan(lower[-1]):
        bb_pos = (close[-1] - float(lower[-1])) / (float(upper[-1]) - float(lower[-1])) if float(upper[-1]) != float(lower[-1]) else 0.5
        bb_score = -30 if bb_pos > 0.9 else (30 if bb_pos < 0.1 else 0)
        score += bb_score * 0.15
        factors["bb_position"] = round(bb_pos, 2)

    # MA trend
    if len(close) >= 60:
        ma20 = np.mean(close[-20:])
        ma60 = np.mean(close[-60:])
        ma_bull = close[-1] > ma20 > ma60
        ma_score = 40 if ma_bull else (-40 if close[-1] < ma20 < ma60 else 0)
        score += ma_score * 0.20
        factors["ma_trend"] = "多頭排列" if ma_bull else ("空頭排列" if close[-1] < ma20 < ma60 else "混合")

    # Volume
    if "volume" in df.columns and len(df) > 20:
        vol = df["volume"].values.flatten()
        vol_ratio = float(vol[-1]) / float(np.mean(vol[-20:])) if np.mean(vol[-20:]) > 0 else 1
        factors["volume_ratio"] = round(vol_ratio, 2)

    return {"score": round(max(-100, min(100, score))), "factors": factors, "close": round(float(close[-1]), 1)}


def scan_stock(symbol: str) -> dict:
    """Scan a single TW stock with entry/SL/TP levels."""
    # Using built-in TA functions (no talib dependency)

    ticker = f"{symbol}.TW"
    df = _fetch_yf(ticker, period="6mo")
    if df is None or len(df) < 30:
        return {"symbol": symbol, "error": "Data unavailable"}

    close = df["close"].values.flatten()
    high = df["high"].values.flatten()
    low = df["low"].values.flatten()

    # Technical score
    tech = calc_technical_score(ticker)

    # ATR for SL/TP
    atr = _atr(high, low, close, period=14)
    atr_val = float(atr[-1]) if not np.isnan(atr[-1]) else float(close[-1]) * 0.02

    price = float(close[-1])
    side = "long" if tech["score"] > 10 else ("short" if tech["score"] < -10 else "neutral")

    levels = {}
    if side == "long":
        levels = {
            "entry": f"{price:.1f}",
            "stop_loss": f"{price - atr_val * 2:.1f}",
            "target_1": f"{price + atr_val * 2:.1f}",
            "target_2": f"{price + atr_val * 4:.1f}",
        }
    elif side == "short":
        levels = {
            "entry": f"{price:.1f}",
            "stop_loss": f"{price + atr_val * 2:.1f}",
            "target_1": f"{price - atr_val * 2:.1f}",
            "target_2": f"{price - atr_val * 4:.1f}",
        }

    return {
        "symbol": symbol,
        "price": price,
        "side": side,
        "tech_score": tech["score"],
        "factors": tech["factors"],
        "levels": levels,
        "atr": round(atr_val, 1),
    }


# ============================================================
# 綜合預測
# ============================================================

def predict() -> dict:
    """Run full Taiwan market prediction."""
    direction = calc_direction_score()
    institutional = calc_institutional_score()
    technical = calc_technical_score()

    composite = (
        direction["score"] * 0.40
        + institutional["score"] * 0.35
        + technical["score"] * 0.25
    )

    if composite > 60:
        bias = "強多 🟢🟢"
    elif composite > 20:
        bias = "弱多 🟢"
    elif composite > -20:
        bias = "中性 ⚪"
    elif composite > -60:
        bias = "弱空 🔴"
    else:
        bias = "強空 🔴🔴"

    # Scan watchlist
    stock_picks = []
    for sym in TW_WATCHLIST[:5]:
        try:
            pick = scan_stock(sym)
            if pick.get("side") != "neutral":
                stock_picks.append(pick)
        except Exception as e:
            logger.warning("Stock scan %s failed: %s", sym, e)

    return {
        "timestamp": datetime.now(TW_TZ).isoformat(),
        "composite_score": round(composite),
        "bias": bias,
        "direction": direction,
        "institutional": institutional,
        "technical": technical,
        "stock_picks": stock_picks,
        "taiex": direction.get("taiex"),
    }


# ============================================================
# Telegram 格式化
# ============================================================

def format_predict_report(result: dict) -> str:
    """Format prediction result for Telegram."""
    d = result["direction"]
    i = result["institutional"]
    t = result["technical"]

    lines = [
        f"🇹🇼 *台股大盤預測* | {datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M')}",
        "",
        f"📊 綜合評分：*{result['composite_score']:+d}* ({result['bias']})",
        f"   加權指數：{result.get('taiex', 'N/A')}",
        "━━━━━━━━━━━━━━━━",
        "",
        f"🔮 *多空環境*：{d['score']:+d}",
    ]

    df = d.get("factors", {})
    ma = df.get("ma_alignment", {})
    if ma:
        ma_str = " ".join(f"MA{k[2:]}{'↑' if v else '↓'}" for k, v in ma.items() if k.startswith("ma") and v is not None)
        lines.append(f"  • 均線: {ma_str}")
    st = df.get("supertrend", {})
    if st:
        lines.append(f"  • Supertrend: {st.get('direction', '?')}")
    intl = df.get("international", {})
    if intl:
        lines.append(f"  • S&P500: {intl.get('sp500_5d', '?')} | 費半: {intl.get('sox_5d', '?')}")
    vix = df.get("vix", {})
    if vix.get("value"):
        lines.append(f"  • VIX: {vix['value']}")
    twd = df.get("twd", {})
    if twd.get("rate"):
        lines.append(f"  • 台幣: {twd['rate']} ({twd.get('change_5d', '')})")

    lines.extend(["", f"💰 *籌碼動向*：{i['score']:+d}"])
    ifact = i.get("factors", {})
    if ifact.get("foreign_net"):
        lines.append(f"  • 外資: {ifact['foreign_net']}")
    if ifact.get("trust_net"):
        lines.append(f"  • 投信: {ifact['trust_net']}")
    if ifact.get("dealers_net"):
        lines.append(f"  • 自營商: {ifact['dealers_net']}")
    if ifact.get("margin_net"):
        lines.append(f"  • 融資: {ifact['margin_net']}")

    lines.extend(["", f"📈 *技術分析*：{t['score']:+d}"])
    tfact = t.get("factors", {})
    if tfact.get("rsi"):
        lines.append(f"  • RSI(14): {tfact['rsi']['value']} ({tfact['rsi']['signal']})")
    if tfact.get("macd"):
        lines.append(f"  • MACD: {tfact['macd']['signal']}")
    if tfact.get("ma_trend"):
        lines.append(f"  • 均線: {tfact['ma_trend']}")
    if tfact.get("adx"):
        lines.append(f"  • ADX: {tfact['adx']['value']} (趨勢{tfact['adx']['trend']})")

    if result.get("stock_picks"):
        lines.extend(["", "🎯 *推薦觀察*："])
        for pick in result["stock_picks"][:3]:
            side_emoji = "📈" if pick["side"] == "long" else "📉"
            lines.append(f"  {side_emoji} {pick['symbol']} | {'做多' if pick['side'] == 'long' else '做空'} | 評分 {pick['tech_score']:+d}")
            if pick.get("levels"):
                lv = pick["levels"]
                lines.append(f"     進場: {lv.get('entry')} | SL: {lv.get('stop_loss')} | TP: {lv.get('target_1')}")

    return "\n".join(lines)


def format_chips_report(result: dict) -> str:
    """Format institutional flow report with derivatives."""
    i = result if "score" in result else calc_institutional_score()
    f = i.get("factors", {})

    lines = [
        f"🏦 *台股籌碼快報* | {datetime.now(TW_TZ).strftime('%Y-%m-%d')}",
        "",
        f"📊 綜合籌碼分數：*{i['score']:+d}*",
        "━━━━━━━━━━━━━━━━",
        "",
        "*現貨三大法人*：",
        f"  外資: {f.get('foreign_net', 'N/A')}",
        f"  投信: {f.get('trust_net', 'N/A')}",
        f"  自營商: {f.get('dealers_net', 'N/A')}",
        f"  融資淨變化: {f.get('margin_net', 'N/A')}",
    ]

    # Derivatives section
    if f.get("futures_foreign_net") or f.get("pc_ratio_oi"):
        lines.extend(["", "*期貨*："])
        if f.get("futures_foreign_net"):
            lines.append(f"  外資台指期淨部位: {f['futures_foreign_net']}")

        lines.extend(["", "*選擇權*："])
        if f.get("pc_ratio_oi"):
            lines.append(f"  P/C Ratio (未平倉): {f['pc_ratio_oi']}")
        if f.get("pc_ratio_vol"):
            lines.append(f"  P/C Ratio (成交量): {f['pc_ratio_vol']}")

        # OTM OI distribution (like wantgoo)
        top5_call = f.get("top5_call", [])
        top5_put = f.get("top5_put", [])

        if top5_call or top5_put:
            expiry_label = f.get("options_expiry", "")
            lines.append("")
            lines.append(f"  _(OTM 選擇權 [{expiry_label}])_")

        if top5_call:
            max_oi = max(item["oi"] for item in top5_call) if top5_call else 1
            lines.extend([
                "",
                f"  🔺 *壓力區 (OTM Call)*",
                f"  最大壓力: *{f.get('max_call_strike', '')}* 點 ({f.get('max_call_oi', '')} 口)",
            ])
            for item in top5_call[:5]:
                bar_len = max(1, int(item["oi"] / max_oi * 10))
                bar = "█" * bar_len + "░" * (10 - bar_len)
                lines.append(f"    {item['strike']:>6,}: {bar} {item['oi']:,}")

        if top5_put:
            max_oi = max(item["oi"] for item in top5_put) if top5_put else 1
            lines.extend([
                "",
                f"  🔻 *支撐區 (OTM Put)*",
                f"  最大支撐: *{f.get('max_put_strike', '')}* 點 ({f.get('max_put_oi', '')} 口)",
            ])
            for item in top5_put[:5]:
                bar_len = max(1, int(item["oi"] / max_oi * 10))
                bar = "█" * bar_len + "░" * (10 - bar_len)
                lines.append(f"    {item['strike']:>6,}: {bar} {item['oi']:,}")

    # Retail futures
    if f.get("retail_net"):
        lines.extend([
            "",
            "*散戶動向（小台指）*：",
            f"  淨部位: {f['retail_net']}",
            f"  判讀: {f.get('retail_sentiment', '')}",
        ])

    # Signals
    signals = f.get("derivatives_signals", [])
    if signals:
        lines.extend(["", "*期權信號*："])
        for sig in signals:
            lines.append(f"  • {sig}")

    return "\n".join(lines)


def format_tech_report() -> str:
    """Format technical analysis report."""
    t = calc_technical_score()
    f = t.get("factors", {})

    lines = [
        f"📉 *台股技術分析* | {datetime.now(TW_TZ).strftime('%Y-%m-%d')}",
        "",
        f"📊 技術分數：*{t['score']:+d}*  |  加權: {t.get('close', 'N/A')}",
        "━━━━━━━━━━━━━━━━",
    ]
    if f.get("rsi"):
        lines.append(f"  RSI(14): {f['rsi']['value']} ({f['rsi']['signal']})")
    if f.get("macd"):
        lines.append(f"  MACD: {f['macd']['signal']} (hist: {f['macd']['histogram']})")
    if f.get("adx"):
        lines.append(f"  ADX: {f['adx']['value']} (趨勢{f['adx']['trend']})")
    if f.get("ma_trend"):
        lines.append(f"  均線: {f['ma_trend']}")
    if f.get("bb_position") is not None:
        lines.append(f"  BB 位置: {f['bb_position']:.0%}")
    if f.get("volume_ratio"):
        lines.append(f"  成交量比: {f['volume_ratio']:.1f}x")

    # Scan top watchlist stocks
    lines.extend(["", "🎯 *觀察股掃描*:"])
    for sym in TW_WATCHLIST[:5]:
        try:
            pick = scan_stock(sym)
            if pick.get("error"):
                continue
            emoji = "🟢" if pick["tech_score"] > 10 else ("🔴" if pick["tech_score"] < -10 else "⚪")
            lines.append(f"  {emoji} {sym} ${pick['price']:.0f} 評分{pick['tech_score']:+d}")
            if pick.get("levels"):
                lv = pick["levels"]
                lines.append(f"     SL:{lv.get('stop_loss')} TP1:{lv.get('target_1')} TP2:{lv.get('target_2')}")
        except Exception:
            pass

    return "\n".join(lines)
