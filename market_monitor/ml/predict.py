"""ML prediction module — load trained models and predict.

Usage:
    from market_monitor.ml.predict import predict_direction
    result = predict_direction("^TWII")
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MODEL_DIR = Path(os.environ.get("DATA_DIR", "data")) / "models"
_CLASS_NAMES = {0: "DOWN", 1: "FLAT", 2: "UP"}
_CLASS_EMOJI = {0: "📉", 1: "➡️", 2: "📈"}

# Feature name → Chinese description
_FEATURE_ZH = {
    "rsi_14": "RSI(14) 相對強弱",
    "rsi_7": "RSI(7) 短期動量",
    "macd_hist": "MACD 柱狀體",
    "macd_cross": "MACD 交叉方向",
    "adx_14": "ADX 趨勢強度",
    "bb_pctb": "布林帶位置 %B",
    "bb_width": "布林帶寬度（波動率）",
    "ma20_dist": "與 20 日均線距離",
    "ma60_dist": "與 60 日均線距離",
    "ma120_dist": "與 120 日均線距離",
    "atr_pct": "ATR 波動率",
    "body_pct": "K 線實體比",
    "upper_wick": "上影線比例",
    "lower_wick": "下影線比例",
    "dist_high_20": "距 20 日高點",
    "dist_low_20": "距 20 日低點",
    "above_st": "Supertrend 方向",
    "roc_5": "5 日漲跌幅",
    "roc_10": "10 日漲跌幅",
    "roc_20": "20 日漲跌幅",
    "mom_align": "多週期動量一致性",
    "roc_accel": "動量加速度",
    "vol_ratio_5_20": "成交量比（5/20 日）",
    "vol_roc": "成交量變化率",
    "obv_slope": "OBV 能量潮方向",
    "pv_diverge": "價量背離信號",
    "macro_sp500_roc5": "S&P500 5 日動量",
    "macro_sox_roc5": "費城半導體 5 日動量",
    "macro_vix": "VIX 恐慌指數",
    "macro_vix_chg": "VIX 變化量",
    "macro_dxy": "美元指數方向",
    "macro_twd_chg": "台幣匯率變化",
    "macro_btc_roc5": "BTC 5 日動量",
    "macro_tnx": "美債 10Y 殖利率",
    "macro_gold_roc5": "黃金 5 日動量",
    "dow": "星期幾",
    "month_sin": "月份（週期）",
    "month_cos": "月份（週期）",
}


def _bar(pct: float, length: int = 10) -> str:
    filled = max(0, min(length, int(pct * length)))
    return "█" * filled + "░" * (length - filled)


def predict_direction(ticker: str, horizons: list[int] = [5, 20]) -> dict:
    """Predict market direction using trained LightGBM models.

    Returns:
        dict with keys: predictions (per horizon), features_used, error
    """
    try:
        import joblib
    except ImportError:
        return {"error": "joblib not installed"}

    from market_monitor.ml.features import compute_features

    safe_name = ticker.replace("^", "").replace("-", "").replace("/", "").lower()
    meta_path = MODEL_DIR / f"{safe_name}_meta.json"

    if not meta_path.exists():
        return {"error": f"No trained model for {ticker}. Run training first."}

    with open(meta_path) as f:
        meta = json.load(f)

    # Fetch latest data
    try:
        import yfinance as yf
        df = yf.download(ticker, period="6mo", progress=False, auto_adjust=True)
        if df is None or len(df) < 30:
            return {"error": f"Insufficient data for {ticker}"}
        df.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df.columns]
    except Exception as e:
        return {"error": f"Data fetch failed: {e}"}

    # Fetch macro
    try:
        from market_monitor.ml.train import fetch_macro
        macro = fetch_macro()
    except Exception:
        macro = {}

    # Compute features
    features = compute_features(df, macro)
    feature_cols = meta.get("features", list(features.columns))

    # Align columns
    for col in feature_cols:
        if col not in features.columns:
            features[col] = 0

    latest_features = features[feature_cols].iloc[-1:].fillna(0)

    predictions = {}
    for h in horizons:
        model_path = MODEL_DIR / f"{safe_name}_h{h}.pkl"
        if not model_path.exists():
            predictions[h] = {"error": f"Model not found: {model_path.name}"}
            continue

        model = joblib.load(model_path)
        proba = model.predict_proba(latest_features)[0]

        # Ensure 3 classes
        if len(proba) < 3:
            proba = np.array([proba[0], 0, proba[-1]])

        pred_class = int(np.argmax(proba))
        confidence = float(proba[pred_class])

        # Direction recommendation
        if confidence < 0.45:
            recommendation = "觀望（信心不足）"
        elif pred_class == 2:
            recommendation = "偏多操作" if confidence > 0.6 else "謹慎偏多"
        elif pred_class == 0:
            recommendation = "偏空操作" if confidence > 0.6 else "謹慎偏空"
        else:
            recommendation = "盤整觀望"

        predictions[h] = {
            "probabilities": {
                "UP": round(float(proba[2]), 3),
                "FLAT": round(float(proba[1]), 3),
                "DOWN": round(float(proba[0]), 3),
            },
            "prediction": _CLASS_NAMES[pred_class],
            "confidence": round(confidence, 3),
            "recommendation": recommendation,
        }

    # Feature importance from latest model
    top_features = {}
    try:
        results = meta.get("results", {})
        for h_key, r in results.items():
            if "top_features" in r:
                top_features = r["top_features"]
                break
    except Exception:
        pass

    return {
        "ticker": ticker,
        "close": round(float(df["close"].iloc[-1]), 2),
        "predictions": predictions,
        "top_features": dict(list(top_features.items())[:5]),
        "model_trained": meta.get("trained_at", "unknown"),
    }


def format_ml_report(result: dict) -> str:
    """Format ML prediction for Telegram."""
    if "error" in result:
        return f"🧠 ML 預測失敗: {result['error']}"

    lines = [
        f"🧠 *ML 預測* | {result['ticker']} ${result['close']}",
        "",
    ]

    horizon_names = {5: "短期（5日）", 20: "中期（20日）", 60: "長期（60日）"}

    for h, pred in result.get("predictions", {}).items():
        h = int(h)
        if "error" in pred:
            lines.append(f"{horizon_names.get(h, f'{h}日')}: {pred['error']}")
            continue

        p = pred["probabilities"]
        lines.extend([
            f"*{horizon_names.get(h, f'{h}日')}*：",
            f"  📈 上漲：{p['UP']*100:.0f}%  {_bar(p['UP'])}",
            f"  📉 下跌：{p['DOWN']*100:.0f}%  {_bar(p['DOWN'])}",
            f"  ➡️ 盤整：{p['FLAT']*100:.0f}%  {_bar(p['FLAT'])}",
            f"  → _{pred['recommendation']}_",
            "",
        ])

    if result.get("top_features"):
        lines.append("*關鍵影響因子 Top 5*：")
        for i, (feat, imp) in enumerate(result["top_features"].items(), 1):
            zh_name = _FEATURE_ZH.get(feat, feat)
            lines.append(f"  {i}. {zh_name} — 影響力 {imp}")

    lines.append(f"\n_模型訓練: {result.get('model_trained', 'N/A')[:10]}_")

    return "\n".join(lines)
