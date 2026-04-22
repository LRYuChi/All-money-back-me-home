"""WalletProfile — scanner 的核心輸出資料結構.

每次 scanner.scan_wallet() 都產出一個 WalletProfile 實例。
透過 to_db_dict() 序列化進 wallet_profiles 表，from_db_row() 反序列化。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


Confidence = Literal["ok", "low_samples", "unknown"]


@dataclass
class FeatureResult:
    """單一特徵的計算結果。所有 feature 一律回傳此型別."""

    feature_name: str
    feature_version: str
    value: Any  # dict / float / str / None — feature 自定義
    confidence: Confidence
    sample_size: int
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "feature_version": self.feature_version,
            "value": self.value,
            "confidence": self.confidence,
            "sample_size": self.sample_size,
            "notes": self.notes,
        }

    @classmethod
    def unknown(cls, name: str, version: str, *, sample_size: int = 0, reason: str = "") -> "FeatureResult":
        """建構一個 'unknown' 結果——所有 feature 在資料不足或計算失敗時應回傳此."""
        return cls(
            feature_name=name,
            feature_version=version,
            value=None,
            confidence="unknown",
            sample_size=sample_size,
            notes=reason,
        )


@dataclass
class WalletProfile:
    """單一錢包在某一次掃描的完整畫像."""

    wallet_address: str
    scanner_version: str
    scanned_at: datetime

    # 第二階段
    passed_coarse_filter: bool
    coarse_filter_reasons: list[str] = field(default_factory=list)

    # 第三階段：核心統計（拆出來方便快查）
    trade_count_90d: int = 0
    resolved_count: int = 0
    cumulative_pnl: float = 0.0
    avg_trade_size: float = 0.0
    win_rate: float = 0.0

    # 第三階段：完整特徵字典（key = feature name）
    features: dict[str, FeatureResult] = field(default_factory=dict)

    # 第四階段：分類
    tier: str = "excluded"               # 量的閘門
    archetypes: list[str] = field(default_factory=list)  # 質的畫像（multi-label）
    risk_flags: list[str] = field(default_factory=list)

    # 元資料
    sample_size_warning: bool = False
    raw_features: dict[str, Any] = field(default_factory=dict)  # 中間計算供未來歸因

    def to_db_dict(self) -> dict[str, Any]:
        """序列化為符合 wallet_profiles schema 的 dict."""
        features_serialized = {name: fr.to_dict() for name, fr in self.features.items()}
        return {
            "wallet_address": self.wallet_address,
            "scanner_version": self.scanner_version,
            "scanned_at": self.scanned_at.isoformat(),
            "passed_coarse_filter": int(self.passed_coarse_filter),
            "coarse_filter_reasons": json.dumps(self.coarse_filter_reasons, ensure_ascii=False),
            "trade_count_90d": self.trade_count_90d,
            "resolved_count": self.resolved_count,
            "cumulative_pnl": self.cumulative_pnl,
            "avg_trade_size": self.avg_trade_size,
            "win_rate": self.win_rate,
            "features_json": json.dumps(features_serialized, ensure_ascii=False, default=str),
            "tier": self.tier,
            "archetypes_json": json.dumps(self.archetypes, ensure_ascii=False),
            "risk_flags_json": json.dumps(self.risk_flags, ensure_ascii=False),
            "sample_size_warning": int(self.sample_size_warning),
            "raw_features_json": json.dumps(self.raw_features, ensure_ascii=False, default=str),
        }

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> "WalletProfile":
        """從 wallet_profiles 一列反序列化."""
        scanned_at_str = row["scanned_at"]
        scanned_at = (
            datetime.fromisoformat(scanned_at_str.replace("Z", "+00:00"))
            if isinstance(scanned_at_str, str)
            else scanned_at_str
        )
        if scanned_at.tzinfo is None:
            scanned_at = scanned_at.replace(tzinfo=timezone.utc)

        features_serialized = _parse_json_field(row.get("features_json"), {})
        features: dict[str, FeatureResult] = {}
        if isinstance(features_serialized, dict):
            for name, fdata in features_serialized.items():
                if not isinstance(fdata, dict):
                    continue
                features[name] = FeatureResult(
                    feature_name=name,
                    feature_version=fdata.get("feature_version", "?"),
                    value=fdata.get("value"),
                    confidence=fdata.get("confidence", "unknown"),
                    sample_size=int(fdata.get("sample_size", 0)),
                    notes=fdata.get("notes", ""),
                )

        return cls(
            wallet_address=row["wallet_address"],
            scanner_version=row["scanner_version"],
            scanned_at=scanned_at,
            passed_coarse_filter=bool(row.get("passed_coarse_filter", 1)),
            coarse_filter_reasons=_parse_json_field(row.get("coarse_filter_reasons"), []),
            trade_count_90d=int(row.get("trade_count_90d") or 0),
            resolved_count=int(row.get("resolved_count") or 0),
            cumulative_pnl=float(row.get("cumulative_pnl") or 0.0),
            avg_trade_size=float(row.get("avg_trade_size") or 0.0),
            win_rate=float(row.get("win_rate") or 0.0),
            features=features,
            tier=row.get("tier") or "excluded",
            archetypes=_parse_json_field(row.get("archetypes_json"), []),
            risk_flags=_parse_json_field(row.get("risk_flags_json"), []),
            sample_size_warning=bool(row.get("sample_size_warning", 0)),
            raw_features=_parse_json_field(row.get("raw_features_json"), {}),
        )


def _parse_json_field(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default
