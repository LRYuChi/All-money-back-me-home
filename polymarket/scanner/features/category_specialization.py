"""CategorySpecializationFeature — 領域專精 (1.5b 首批優先).

回答的問題：這個錢包是「全領域通才」還是「某個領域的專家」？

計算邏輯：
    1. 將錢包的已結算倉位按市場類別分組（政治/體育/加密/娛樂/...）
    2. 對每個有足夠樣本的類別，計算勝率
    3. 與錢包整體勝率比較，超過 lift 門檻者標為 specialist
    4. 同時記錄主要類別（佔比最高的）與類別覆蓋廣度

回傳資料結構：
    {
      "categories": {
        "Politics":  {"trades": 24, "resolved": 18, "win_rate": 0.72, "is_specialist": true},
        "Sports":    {"trades": 12, "resolved": 10, "win_rate": 0.50, "is_specialist": false},
        ...
      },
      "primary_category": "Politics",       # 倉位數最多
      "specialist_categories": ["Politics"],
      "category_count": 2,
      "unknown_ratio": 0.05,                # category 無法判定的倉位比例
    }

confidence 規則：
    - 整體已結算倉位 < min_total_resolved → low_samples
    - unknown_ratio > max_unknown_ratio → low_samples（categories 表覆蓋不足）
    - 否則 → ok
"""

from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal
from typing import Any

from polymarket.scanner.features.base import BaseFeature, ScanContext
from polymarket.scanner.profile import FeatureResult

logger = logging.getLogger(__name__)

UNKNOWN_CATEGORY = "(unknown)"


class CategorySpecializationFeature(BaseFeature):
    name = "category_specialization"
    version = "1.0"
    min_samples = 10  # 整體已結算倉位門檻；單一類別內另有 min_samples_per_category

    def _compute(self, ctx: ScanContext) -> FeatureResult:
        cfg = ctx.pre_reg["scanner"]["features"]["thresholds"]["category_specialization"]
        min_per_cat = int(cfg["min_samples_per_category"]["value"])
        min_total = int(cfg["min_total_resolved"]["value"])
        lift_threshold = float(cfg["specialist_win_rate_lift"]["value"])
        max_unknown = float(cfg["max_unknown_ratio"]["value"])

        # 只看已結算倉位（is_winning 才有意義）
        resolved = [p for p in ctx.positions if p.is_resolved]
        total_resolved = len(resolved)

        if total_resolved < min_total:
            return FeatureResult(
                feature_name=self.name,
                feature_version=self.version,
                value={
                    "categories": {},
                    "primary_category": None,
                    "specialist_categories": [],
                    "category_count": 0,
                    "unknown_ratio": 0.0,
                    "total_resolved": total_resolved,
                },
                confidence="low_samples",
                sample_size=total_resolved,
                notes=f"need >= {min_total} resolved positions, got {total_resolved}",
            )

        # 整體勝率（baseline）
        overall_wins = sum(1 for p in resolved if p.is_winning)
        overall_win_rate = overall_wins / total_resolved if total_resolved else 0.0

        # 按 category 分組
        by_cat: dict[str, list] = defaultdict(list)
        unknown_count = 0
        for p in resolved:
            cat = ctx.market_categories.get(p.condition_id, "")
            if not cat:
                cat = UNKNOWN_CATEGORY
                unknown_count += 1
            by_cat[cat].append(p)

        unknown_ratio = unknown_count / total_resolved if total_resolved else 0.0

        # 計算每個類別的統計
        categories: dict[str, dict[str, Any]] = {}
        for cat, positions in by_cat.items():
            cat_resolved = len(positions)
            cat_wins = sum(1 for p in positions if p.is_winning)
            cat_win_rate = cat_wins / cat_resolved if cat_resolved else 0.0
            cat_notional = float(sum((p.initial_value for p in positions), Decimal(0)))

            is_specialist = (
                cat != UNKNOWN_CATEGORY
                and cat_resolved >= min_per_cat
                and (cat_win_rate - overall_win_rate) >= lift_threshold
            )

            categories[cat] = {
                "resolved": cat_resolved,
                "wins": cat_wins,
                "win_rate": round(cat_win_rate, 4),
                "notional": round(cat_notional, 2),
                "is_specialist": is_specialist,
                "sufficient_samples": cat_resolved >= min_per_cat,
            }

        # 主類別：倉位數最多（排除 unknown）
        named = {k: v for k, v in categories.items() if k != UNKNOWN_CATEGORY}
        primary = max(named.items(), key=lambda kv: kv[1]["resolved"])[0] if named else None
        specialists = sorted(
            (k for k, v in categories.items() if v["is_specialist"]),
            key=lambda k: -categories[k]["win_rate"],
        )

        # confidence
        if unknown_ratio > max_unknown:
            confidence = "low_samples"
            notes = f"unknown_ratio={unknown_ratio:.2f} > {max_unknown:.2f} (markets table needs more coverage)"
        else:
            confidence = "ok"
            notes = f"baseline_win_rate={overall_win_rate:.2%}, lift_threshold={lift_threshold:.0%}"

        return FeatureResult(
            feature_name=self.name,
            feature_version=self.version,
            value={
                "categories": categories,
                "primary_category": primary,
                "specialist_categories": specialists,
                "category_count": len(named),
                "unknown_ratio": round(unknown_ratio, 4),
                "overall_win_rate": round(overall_win_rate, 4),
                "total_resolved": total_resolved,
            },
            confidence=confidence,
            sample_size=total_resolved,
            notes=notes,
        )
