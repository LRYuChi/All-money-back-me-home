"""Supabase client singleton for the API service."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

try:
    from supabase import create_client, Client
    _HAS_SUPABASE = True
except ImportError:
    _HAS_SUPABASE = False
    Client = Any  # type: ignore[assignment,misc]


@lru_cache(maxsize=1)
def get_supabase() -> Client | None:
    """Return a Supabase client, or None if not configured or SDK not installed."""
    if not _HAS_SUPABASE:
        return None

    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "")

    if not url or not key:
        return None

    return create_client(url, key)
