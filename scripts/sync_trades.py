"""
Sync trades from Freqtrade REST API into data/paper_trades.json.

Run: python scripts/sync_trades.py
"""
import json
import os
import sys
import urllib.request
import base64
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
FREQTRADE_URL = os.environ.get("FT_API_URL", "http://127.0.0.1:8080")
FREQTRADE_USER = os.environ.get("FT_USER", "freqtrade")
FREQTRADE_PASS = os.environ.get("FT_PASS", "freqtrade")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
STATE_FILE = DATA_DIR / "paper_trades.json"

INITIAL_CAPITAL = 300.0


# ---------------------------------------------------------------------------
# Freqtrade API helpers
# ---------------------------------------------------------------------------

def _basic_auth_header() -> str:
    """Return HTTP Basic auth header value."""
    creds = base64.b64encode(
        f"{FREQTRADE_USER}:{FREQTRADE_PASS}".encode()
    ).decode()
    return f"Basic {creds}"


def _api_request(
    path: str,
    method: str = "GET",
    token: str | None = None,
    data: dict | None = None,
) -> dict | list:
    """Make a request to Freqtrade API."""
    url = f"{FREQTRADE_URL}{path}"
    headers: dict[str, str] = {"Content-Type": "application/json"}

    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def login() -> str:
    """Login to Freqtrade and return access token."""
    url = f"{FREQTRADE_URL}/api/v1/token/login"
    headers = {
        "Authorization": _basic_auth_header(),
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=None, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["access_token"]


def get_open_trades(token: str) -> list[dict]:
    """GET /api/v1/status - open trades."""
    result = _api_request("/api/v1/status", token=token)
    if isinstance(result, dict):
        return []
    return result


def get_closed_trades(token: str, limit: int = 50) -> list[dict]:
    """GET /api/v1/trades?limit=N - trade history."""
    result = _api_request(f"/api/v1/trades?limit={limit}", token=token)
    if isinstance(result, dict):
        return result.get("trades", [])
    return result


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state() -> dict:
    """Load paper_trades.json state."""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "capital": INITIAL_CAPITAL,
        "initial_capital": INITIAL_CAPITAL,
        "open_positions": [],
        "closed_trades": [],
        "scan_history": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def save_state(state: dict) -> None:
    """Save state to paper_trades.json."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------
# Mapping Freqtrade trades -> unified format
# ---------------------------------------------------------------------------

def _map_ft_open(ft: dict) -> dict:
    """Map a Freqtrade open trade to our position format."""
    pair = ft.get("pair", "UNKNOWN")
    trade_id = ft.get("trade_id", 0)
    is_short = ft.get("is_short", False)

    stop_loss = ft.get("stop_loss_abs") or ft.get("stoploss_current_dist_ratio", 0)
    entry_price = ft.get("open_rate", 0.0)

    return {
        "id": f"ft_{trade_id}",
        "symbol": pair,
        "direction": "short" if is_short else "long",
        "strategy": ft.get("strategy", "freqtrade"),
        "entry_price": round(float(entry_price), 8),
        "stop_loss": round(float(stop_loss), 8) if stop_loss else 0.0,
        "take_profit_levels": [],
        "position_size_usd": round(float(ft.get("stake_amount", 0)), 4),
        "leverage": float(ft.get("leverage", 1)),
        "confidence": 0.0,
        "reason": ft.get("enter_tag", "freqtrade signal"),
        "entry_time": ft.get("open_date", datetime.now(timezone.utc).isoformat()),
        "status": "open",
        "source": "freqtrade",
    }


def _map_ft_closed(ft: dict) -> dict:
    """Map a Freqtrade closed trade to our closed trade format."""
    pair = ft.get("pair", "UNKNOWN")
    trade_id = ft.get("trade_id", 0)
    is_short = ft.get("is_short", False)

    entry_price = ft.get("open_rate", 0.0)
    exit_price = ft.get("close_rate", 0.0)
    stop_loss = ft.get("stop_loss_abs", 0.0)
    profit_abs = ft.get("profit_abs", 0.0)
    profit_ratio = ft.get("profit_ratio", 0.0)

    exit_reason = ft.get("exit_reason", "unknown")
    # Map Freqtrade exit reasons to Chinese
    reason_map = {
        "stop_loss": "止損觸發",
        "stoploss_on_exchange": "止損觸發",
        "trailing_stop_loss": "移動止損",
        "roi": "ROI 止盈",
        "exit_signal": "出場信號",
        "force_exit": "強制平倉",
        "emergency_exit": "緊急平倉",
    }
    exit_reason_zh = reason_map.get(exit_reason, exit_reason)

    return {
        "id": f"ft_{trade_id}",
        "symbol": pair,
        "direction": "short" if is_short else "long",
        "strategy": ft.get("strategy", "freqtrade"),
        "entry_price": round(float(entry_price), 8),
        "stop_loss": round(float(stop_loss), 8) if stop_loss else 0.0,
        "take_profit_levels": [],
        "position_size_usd": round(float(ft.get("stake_amount", 0)), 4),
        "leverage": float(ft.get("leverage", 1)),
        "confidence": 0.0,
        "reason": ft.get("enter_tag", "freqtrade signal"),
        "entry_time": ft.get("open_date", ""),
        "exit_price": round(float(exit_price), 8) if exit_price else 0.0,
        "exit_time": ft.get("close_date", ""),
        "exit_reason": exit_reason_zh,
        "pnl_usd": round(float(profit_abs), 4),
        "pnl_pct": round(float(profit_ratio) * 100, 4),
        "status": "closed",
        "source": "freqtrade",
    }


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

def merge_trades(state: dict, ft_open: list[dict], ft_closed: list[dict]) -> dict:
    """Merge Freqtrade trades into the unified state, avoiding duplicates."""
    # Build sets of existing IDs for fast lookup
    existing_open_ids = {p["id"] for p in state["open_positions"]}
    existing_closed_ids = {t["id"] for t in state["closed_trades"]}

    added_open = 0
    updated_open = 0
    added_closed = 0

    # --- Process open trades ---
    ft_open_ids = set()
    for ft in ft_open:
        pos = _map_ft_open(ft)
        ft_open_ids.add(pos["id"])

        if pos["id"] in existing_open_ids:
            # Update existing open position (price might have changed)
            for i, existing in enumerate(state["open_positions"]):
                if existing["id"] == pos["id"]:
                    state["open_positions"][i] = pos
                    updated_open += 1
                    break
        elif pos["id"] not in existing_closed_ids:
            # New open position (and not already closed)
            state["open_positions"].append(pos)
            added_open += 1

    # Remove ft_ positions that are no longer open in Freqtrade
    # (they were closed on Freqtrade side)
    state["open_positions"] = [
        p for p in state["open_positions"]
        if not p["id"].startswith("ft_") or p["id"] in ft_open_ids
    ]

    # --- Process closed trades ---
    for ft in ft_closed:
        if not ft.get("is_open", True):
            trade = _map_ft_closed(ft)
            if trade["id"] not in existing_closed_ids:
                state["closed_trades"].append(trade)
                added_closed += 1

    return {
        "added_open": added_open,
        "updated_open": updated_open,
        "added_closed": added_closed,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    now = datetime.now(timezone.utc)
    print(f"\n{'=' * 60}")
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S UTC')}] Freqtrade Trade Sync")
    print(f"{'=' * 60}")

    # 1. Login
    print("[1/4] Logging in to Freqtrade API...")
    try:
        token = login()
        print("  OK - token acquired")
    except Exception as e:
        print(f"  FAILED: {e}")
        print("\n  Make sure Freqtrade is running at", FREQTRADE_URL)
        sys.exit(1)

    # 2. Fetch open trades
    print("[2/4] Fetching open trades...")
    ft_open = get_open_trades(token)
    print(f"  Found {len(ft_open)} open trade(s)")

    # 3. Fetch closed trades
    print("[3/4] Fetching trade history...")
    ft_closed = get_closed_trades(token, limit=50)
    print(f"  Found {len(ft_closed)} trade(s) in history")

    # 4. Load existing state and merge
    print("[4/4] Merging into paper_trades.json...")
    state = load_state()

    stats = merge_trades(state, ft_open, ft_closed)

    save_state(state)

    # Summary
    print(f"\n{'=' * 60}")
    print("[Sync Summary]")
    print(f"  New open positions added:   {stats['added_open']}")
    print(f"  Open positions updated:     {stats['updated_open']}")
    print(f"  New closed trades added:    {stats['added_closed']}")
    print(f"  Total open positions:       {len(state['open_positions'])}")
    print(f"  Total closed trades:        {len(state['closed_trades'])}")
    print(f"  Capital: ${state['capital']:.2f}")
    print(f"  State saved to: {STATE_FILE}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
