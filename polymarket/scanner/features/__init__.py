"""Scanner features registry.

每個 feature 是一個 BaseFeature 子類別，註冊在這裡。
Scanner.scan() 透過 pre_registered.yaml 的 enabled_in_version 決定要跑哪些。
"""

from __future__ import annotations

from polymarket.scanner.features.base import BaseFeature, ScanContext
from polymarket.scanner.features.brier_calibration import BrierCalibrationFeature
from polymarket.scanner.features.category_specialization import CategorySpecializationFeature
from polymarket.scanner.features.core import CoreStatsFeature
from polymarket.scanner.features.steady_growth import SteadyGrowthFeature
from polymarket.scanner.features.time_slice_consistency import TimeSliceConsistencyFeature

# 註冊表：feature name → BaseFeature 實例
# 啟用順序由 pre_registered.yaml 的 scanner.features.enabled_in_version 決定，
# 此處只是「全部可選清單」。新 feature 加在這裡，要在 yaml 對應 version 啟用才會跑。
REGISTRY: dict[str, BaseFeature] = {
    CoreStatsFeature.name: CoreStatsFeature(),
    CategorySpecializationFeature.name: CategorySpecializationFeature(),
    TimeSliceConsistencyFeature.name: TimeSliceConsistencyFeature(),
    SteadyGrowthFeature.name: SteadyGrowthFeature(),
    BrierCalibrationFeature.name: BrierCalibrationFeature(),
}


def get(name: str) -> BaseFeature | None:
    """取得已註冊的 feature。未知名稱回傳 None（呼叫端記 warning 即可）."""
    return REGISTRY.get(name)


def all_names() -> list[str]:
    return sorted(REGISTRY.keys())


__all__ = ["BaseFeature", "ScanContext", "REGISTRY", "get", "all_names"]
