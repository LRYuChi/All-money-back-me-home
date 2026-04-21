"""Polymarket 模組設定 — endpoints, env vars, pre-registered 門檻讀取.

所有門檻值的唯一真實來源是 polymarket/config/pre_registered.yaml。
代碼禁止 inline hardcode 業務門檻。基礎設施常量（URL、timeout）可寫在此。
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# === API Endpoints ===
CLOB_REST_URL = "https://clob.polymarket.com"
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws"
GAMMA_REST_URL = "https://gamma-api.polymarket.com"
DATA_API_URL = "https://data-api.polymarket.com"

# === HTTP settings ===
DEFAULT_TIMEOUT_S = 15.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_BASE = 0.5  # seconds, exponential

# === Storage ===
# Phase 0 預設本地 SQLite；Phase 1 改用 Supabase PostgreSQL（env var 切換）
_repo_root = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", _repo_root / "data"))
SQLITE_PATH = DATA_DIR / "polymarket.db"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

# === Pre-registered config ===
PRE_REGISTERED_YAML = Path(__file__).resolve().parent / "config" / "pre_registered.yaml"


@lru_cache(maxsize=1)
def load_pre_registered() -> dict[str, Any]:
    """載入 pre_registered.yaml — 所有業務門檻的單一來源."""
    if not PRE_REGISTERED_YAML.exists():
        raise FileNotFoundError(
            f"pre_registered.yaml 不存在於 {PRE_REGISTERED_YAML}。"
            "這是系統憲法，必須存在。請參閱 docs/polymarket/architecture.md §第一章原則 3。"
        )
    with open(PRE_REGISTERED_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_threshold(path: str) -> Any:
    """從 pre_registered.yaml 讀取門檻，路徑如 'whale_tiers.A.min_win_rate.value'."""
    cfg = load_pre_registered()
    node: Any = cfg
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            raise KeyError(f"pre_registered.yaml 找不到路徑: {path}")
        node = node[key]
    return node


def use_supabase() -> bool:
    """是否切換到 Supabase（由 env var 觸發）."""
    return bool(SUPABASE_URL and SUPABASE_KEY)
