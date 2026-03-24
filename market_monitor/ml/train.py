"""Train LightGBM models for market direction prediction.

Usage:
    python -m market_monitor.ml.train --ticker ^TWII --horizons 5,20
    python -m market_monitor.ml.train --ticker BTC-USD --horizons 5,20,60
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

MODEL_DIR = Path(os.environ.get("DATA_DIR", "data")) / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)


def fetch_data(ticker: str, period: str = "5y") -> pd.DataFrame | None:
    """Fetch OHLCV data from yfinance."""
    try:
        import yfinance as yf
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if df is not None and len(df) > 100:
            df.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df.columns]
            return df
    except Exception as e:
        logger.error("Data fetch failed for %s: %s", ticker, e)
    return None


def fetch_macro() -> dict:
    """Fetch macro indicators for feature enrichment."""
    macro = {}
    try:
        import yfinance as yf
        tickers = {"^GSPC": "sp500", "^SOX": "sox", "^VIX": "vix",
                    "DX-Y.NYB": "dxy", "GC=F": "gold", "BTC-USD": "btc", "^TNX": "tnx"}
        data = yf.download(list(tickers.keys()), period="1mo", progress=False, auto_adjust=True)
        if data is not None and len(data) > 5:
            close = data["Close"] if "Close" in data.columns else data[("close",)]
            for yf_ticker, name in tickers.items():
                try:
                    col = close[yf_ticker] if yf_ticker in close.columns else None
                    if col is not None and len(col.dropna()) > 5:
                        vals = col.dropna().values
                        macro[f"{name}_roc5"] = float((vals[-1] / vals[-5] - 1) * 100) if len(vals) >= 5 else 0
                        if name == "vix":
                            macro["vix"] = float(vals[-1])
                            macro["vix_chg"] = float(vals[-1] - vals[-5]) if len(vals) >= 5 else 0
                except Exception:
                    pass
    except Exception as e:
        logger.warning("Macro fetch failed: %s", e)
    return macro


def train_model(ticker: str, horizons: list[int] = [5, 20],
                test_months: int = 6, threshold: float = 0.02):
    """Train LightGBM models with walk-forward validation.

    Args:
        ticker: yfinance ticker (e.g. ^TWII, BTC-USD)
        horizons: Prediction horizons in trading days
        test_months: Number of months for OOS testing
        threshold: UP/DOWN classification threshold
    """
    try:
        import lightgbm as lgb
        from sklearn.metrics import accuracy_score, classification_report
        import joblib
    except ImportError:
        logger.error("Missing dependencies. Run: pip install lightgbm scikit-learn joblib")
        return

    from market_monitor.ml.features import compute_features, create_labels

    # Fetch data
    logger.info("Fetching data for %s...", ticker)
    df = fetch_data(ticker, period="5y")
    if df is None:
        logger.error("No data for %s", ticker)
        return

    macro = fetch_macro()
    logger.info("Data: %d rows, macro keys: %s", len(df), list(macro.keys()))

    # Compute features
    features = compute_features(df, macro)
    labels = create_labels(df, horizons=horizons, threshold=threshold)

    # Combine and drop NaN
    combined = pd.concat([features, labels], axis=1).dropna()
    logger.info("Combined dataset: %d rows, %d features", len(combined), len(features.columns))

    if len(combined) < 200:
        logger.error("Not enough data (%d rows). Need at least 200.", len(combined))
        return

    feature_cols = [c for c in features.columns if c in combined.columns]

    # Time-series split: last N months = test
    split_date = combined.index[-1] - pd.DateOffset(months=test_months)
    train = combined[combined.index <= split_date]
    test = combined[combined.index > split_date]
    logger.info("Train: %d rows (to %s), Test: %d rows (from %s)",
                len(train), split_date.strftime("%Y-%m-%d"),
                len(test), test.index[0].strftime("%Y-%m-%d") if len(test) > 0 else "N/A")

    results = {}
    safe_name = ticker.replace("^", "").replace("-", "").replace("/", "").lower()

    for h in horizons:
        label_col = f"label_{h}"
        if label_col not in combined.columns:
            continue

        X_train = train[feature_cols]
        y_train = train[label_col].astype(int)
        X_test = test[feature_cols]
        y_test = test[label_col].astype(int)

        if len(y_train.unique()) < 2:
            logger.warning("Horizon %d: only %d classes in training data, skipping", h, len(y_train.unique()))
            continue

        # Train
        model = lgb.LGBMClassifier(
            objective="multiclass",
            num_class=3,
            n_estimators=200,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_samples=20,
            random_state=42,
            verbose=-1,
        )

        logger.info("Training horizon=%d model...", h)
        model.fit(X_train, y_train)

        # Evaluate
        y_pred = model.predict(X_test)
        acc = accuracy_score(y_test, y_pred)

        # Direction accuracy (UP vs DOWN only, ignoring FLAT)
        mask = (y_test != 1) & (y_pred != 1)
        dir_acc = accuracy_score(y_test[mask], y_pred[mask]) if mask.sum() > 10 else 0

        # Feature importance
        importance = dict(sorted(
            zip(feature_cols, model.feature_importances_),
            key=lambda x: x[1], reverse=True
        )[:10])

        logger.info("Horizon %d: Accuracy=%.1f%%, Direction=%.1f%%, Samples=%d",
                     h, acc * 100, dir_acc * 100, len(y_test))
        logger.info("  Top features: %s", list(importance.keys())[:5])

        # Class distribution
        for cls, name in [(0, "DOWN"), (1, "FLAT"), (2, "UP")]:
            n_train = (y_train == cls).sum()
            n_test = (y_test == cls).sum()
            logger.info("  %s: train=%d (%.0f%%), test=%d (%.0f%%)",
                        name, n_train, n_train / len(y_train) * 100,
                        n_test, n_test / len(y_test) * 100 if len(y_test) > 0 else 0)

        # Save model
        model_path = MODEL_DIR / f"{safe_name}_h{h}.pkl"
        joblib.dump(model, model_path)
        logger.info("Model saved: %s", model_path)

        results[f"horizon_{h}"] = {
            "accuracy": round(acc, 4),
            "direction_accuracy": round(dir_acc, 4),
            "test_samples": len(y_test),
            "top_features": importance,
            "class_distribution": {
                "DOWN": int((y_test == 0).sum()),
                "FLAT": int((y_test == 1).sum()),
                "UP": int((y_test == 2).sum()),
            },
        }

    # Save feature list + metadata
    meta = {
        "ticker": ticker,
        "features": feature_cols,
        "horizons": horizons,
        "threshold": threshold,
        "train_end": str(split_date.date()),
        "test_start": str(test.index[0].date()) if len(test) > 0 else None,
        "trained_at": datetime.utcnow().isoformat(),
        "results": results,
    }
    meta_path = MODEL_DIR / f"{safe_name}_meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, default=str)
    logger.info("Metadata saved: %s", meta_path)

    # Print summary
    print("\n" + "=" * 60)
    print(f"Training Complete: {ticker}")
    print("=" * 60)
    for h, r in results.items():
        print(f"\n{h}:")
        print(f"  Accuracy: {r['accuracy']*100:.1f}%")
        print(f"  Direction Accuracy: {r['direction_accuracy']*100:.1f}%")
        print(f"  Test Samples: {r['test_samples']}")
        print(f"  Top Features:")
        for feat, imp in list(r["top_features"].items())[:5]:
            print(f"    {feat}: {imp}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train market prediction model")
    parser.add_argument("--ticker", default="^TWII", help="yfinance ticker")
    parser.add_argument("--horizons", default="5,20", help="Prediction horizons (comma-separated)")
    parser.add_argument("--threshold", type=float, default=0.02, help="UP/DOWN threshold")
    parser.add_argument("--test-months", type=int, default=6, help="OOS test months")
    args = parser.parse_args()

    horizons = [int(x) for x in args.horizons.split(",")]
    train_model(args.ticker, horizons=horizons, threshold=args.threshold, test_months=args.test_months)
