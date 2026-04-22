"""Scanner features registry.

每個 feature 是一個 BaseFeature 子類別，註冊在這裡。
Scanner.scan() 透過 pre_registered.yaml 的 enabled_in_version 決定要跑哪些。
"""

from __future__ import annotations

from polymarket.scanner.features.base import BaseFeature, ScanContext
from polymarket.scanner.features.core import CoreStatsFeature

# 註冊表：feature name → BaseFeature 實例
REGISTRY: dict[str, BaseFeature] = {
    CoreStatsFeature.name: CoreStatsFeature(),
}


def get(name: str) -> BaseFeature | None:
    """取得已註冊的 feature。未知名稱回傳 None（呼叫端記 warning 即可）."""
    return REGISTRY.get(name)


def all_names() -> list[str]:
    return sorted(REGISTRY.keys())


__all__ = ["BaseFeature", "ScanContext", "REGISTRY", "get", "all_names"]
