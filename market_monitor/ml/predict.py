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
        lines.append("*特徵重要性 Top 5*：")
        for i, (feat, imp) in enumerate(result["top_features"].items(), 1):
            lines.append(f"  {i}. {feat} — {imp}")

    lines.append(f"\n_模型訓練: {result.get('model_trained', 'N/A')[:10]}_")

    return "\n".join(lines)
