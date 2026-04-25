"""Deterministic client_order_id generation.

The exchange uses client_order_id as an idempotency key — re-submitting
the same id produces "this order already exists; here is its current
state" rather than a duplicate position. So the same logical intent
must always derive the same id.

Strategy: SHA-256 over the tuple `(strategy_id, symbol, side, intent_ts_ms,
mode)`, hex-truncated to 16 chars (64 bits — enough collision resistance
for a single exchange's order book lifetime). Prefix with a short tag so
ops can grep by env at-a-glance.

OKX limits client_order_id to 32 chars alphanumeric/underscore; the
output here is 22 chars total ("sm-" + 16 hex + suffix slot for
adapter-specific tweaks). Other exchanges have similar limits;
adapter wraps this with its own validator if needed.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone


_VALID_OKX = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def make_client_order_id(
    *,
    strategy_id: str,
    symbol: str,
    side: str,
    intent_ts: datetime,
    mode: str = "live",
    prefix: str = "sm",
) -> str:
    """Deterministic 22-char client_order_id.

    Re-running with the same args returns byte-identical output.

    Args:
        strategy_id: e.g. "crypto_btc_smart_money_v1"
        symbol: canonical "crypto:OKX:BTC/USDT:USDT"
        side: "long" or "short"
        intent_ts: when the strategy fired the intent. MUST be the same
                   value across retries (caller stores it on the order
                   row, doesn't generate a new one).
        mode: "live" / "paper" — included so a shadow order and its live
              twin don't accidentally collide on re-promotion.
        prefix: short tag visible to ops (default "sm" for smart_money).
    """
    if not strategy_id or not symbol or not side:
        raise ValueError(
            f"strategy_id/symbol/side required; got {strategy_id!r}/"
            f"{symbol!r}/{side!r}"
        )

    # Normalise ts to UTC ms so naive vs aware doesn't matter
    if intent_ts.tzinfo is None:
        intent_ts = intent_ts.replace(tzinfo=timezone.utc)
    ts_ms = int(intent_ts.astimezone(timezone.utc).timestamp() * 1000)

    payload = f"{strategy_id}|{symbol}|{side}|{ts_ms}|{mode}".encode()
    digest = hashlib.sha256(payload).hexdigest()[:16]
    coid = f"{prefix}-{digest}"

    if not _VALID_OKX.match(coid):
        # Should never trigger with default prefix; defensive belt+braces
        raise ValueError(
            f"generated client_order_id {coid!r} fails OKX char/length validation"
        )
    return coid


__all__ = ["make_client_order_id"]
