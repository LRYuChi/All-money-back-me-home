"""Seed 錢包來源:yaml 檔 + 已知鯨魚清單.

HL 沒有公開 leaderboard REST endpoint,因此 seed 策略:
1. `seeds.yaml`(git-tracked,社群已知的 active trader 清單)
2. `watchlist.yaml`(本地 gitignored,使用者自己加的)
3. (Phase 4 加入) WS 訂閱實時 fills,自動發現活躍地址
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_EVM_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def is_valid_address(addr: str) -> bool:
    return bool(_EVM_ADDR_RE.match(addr))


def load_seed_file(path: Path) -> list[str]:
    """Load wallet addresses from yaml file.

    Expected format:
        wallets:
          - address: "0x..."
            name: "optional label"
          - "0x..."            # shorthand
    """
    if not path.exists():
        logger.debug("seed file %s missing, returning empty", path)
        return []

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    raw = data.get("wallets", [])
    addresses: list[str] = []
    for item in raw:
        if isinstance(item, str):
            addr = item.strip()
        elif isinstance(item, dict):
            addr = str(item.get("address", "")).strip()
        else:
            continue
        if is_valid_address(addr):
            addresses.append(addr.lower())
        else:
            logger.warning("invalid address in seed file: %r", addr)
    # dedup preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for a in addresses:
        if a not in seen:
            deduped.append(a)
            seen.add(a)
    return deduped


def default_seed_paths() -> list[Path]:
    """預設搜尋順序."""
    root = Path(__file__).resolve().parents[2]
    return [
        root / "smart_money" / "data" / "seeds.yaml",     # git-tracked
        root / "data" / "smart_money" / "watchlist.yaml",  # gitignored
    ]


def load_default_seeds() -> list[str]:
    all_addresses: list[str] = []
    for p in default_seed_paths():
        all_addresses.extend(load_seed_file(p))
    # dedup
    seen: set[str] = set()
    out: list[str] = []
    for a in all_addresses:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


__all__ = [
    "default_seed_paths",
    "is_valid_address",
    "load_default_seeds",
    "load_seed_file",
]
