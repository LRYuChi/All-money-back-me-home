"""機構報告自動收集與 AI 摘要系統.

收集三類報告：宏觀經濟、投行/金融新聞、加密市場。
用 Claude API 生成繁體中文精華摘要。

Usage:
    python -m market_monitor.report_collector          # 收集 + 摘要
    python -m market_monitor.report_collector --fetch   # 只收集
    python -m market_monitor.report_collector --digest  # 只生成摘要
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Output paths
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DIGEST_FILE = DATA_DIR / "reports" / "institutional_digest.json"
SUMMARY_FILE = DATA_DIR / "reports" / "digest_summary.md"

# API keys (optional — graceful degradation if missing)
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
CRYPTOPANIC_API_KEY = os.environ.get("CRYPTOPANIC_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


@dataclass
class Report:
    """Single institutional report / news item."""
    source: str           # "finnhub", "cryptopanic", "fred", "glassnode"
    category: str         # "macro", "crypto", "sentiment", "institutional"
    title: str
    content: str          # Summary or full text
    sentiment: str = "neutral"  # "bullish", "bearish", "neutral"
    relevance: float = 0.5      # 0-1
    timestamp: str = ""
    url: str = ""


# Keyword-based sentiment detection for news headlines
_BULLISH_KW = {
    "rally", "surge", "bullish", "upgrade", "accumulate", "buy", "inflows",
    "breakout", "recovery", "growth", "soars", "jumps", "gains", "record high",
    "approval", "adoption", "partnership", "launch", "etf approved",
}
_BEARISH_KW = {
    "crash", "bearish", "layoff", "hack", "ban", "probe", "downgrade", "sell",
    "plunge", "drops", "falls", "liquidation", "fraud", "investigation",
    "sanctions", "default", "recession", "collapse", "slump", "attack",
}


def _detect_sentiment(text: str) -> str:
    """Detect sentiment from text using keyword matching."""
    lower = text.lower()
    bull = sum(1 for kw in _BULLISH_KW if kw in lower)
    bear = sum(1 for kw in _BEARISH_KW if kw in lower)
    if bull > bear:
        return "bullish"
    elif bear > bull:
        return "bearish"
    return "neutral"


# =============================================
# Data Source Fetchers
# =============================================

def _http_get_json(url: str, headers: dict | None = None, timeout: int = 10) -> dict | list | None:
    """Safe HTTP GET returning parsed JSON, or None on failure."""
    try:
        req = urllib.request.Request(url, headers=headers or {"User-Agent": "AMBMH/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.warning("HTTP fetch failed %s: %s", url, e)
        return None


def fetch_economic_calendar(days_ahead: int = 7) -> list[Report]:
    """Fetch upcoming economic events from Finnhub (requires premium plan)."""
    if not FINNHUB_API_KEY:
        logger.info("FINNHUB_API_KEY not set — skipping economic calendar")
        return []

    now = datetime.now(timezone.utc)
    from_date = now.strftime("%Y-%m-%d")
    to_date = (now + __import__("datetime").timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    url = (
        f"https://finnhub.io/api/v1/calendar/economic"
        f"?from={from_date}&to={to_date}&token={FINNHUB_API_KEY}"
    )
    data = _http_get_json(url)
    if not data or "economicCalendar" not in data:
        return []

    reports = []
    # Filter for high-impact events
    high_impact = {"FOMC", "CPI", "NFP", "GDP", "PPI", "Retail Sales", "Unemployment"}
    for event in data["economicCalendar"]:
        event_name = event.get("event", "")
        country = event.get("country", "")
        if country != "US":
            continue
        # Check if high impact
        is_important = any(kw.lower() in event_name.lower() for kw in high_impact)
        if not is_important and event.get("impact", "") != "high":
            continue

        actual = event.get("actual", "")
        estimate = event.get("estimate", "")
        prev = event.get("prev", "")

        content = f"{event_name} ({country})"
        if actual:
            content += f" | 實際: {actual}"
        if estimate:
            content += f" | 預期: {estimate}"
        if prev:
            content += f" | 前值: {prev}"

        # Determine sentiment from actual vs estimate
        sentiment = "neutral"
        if actual and estimate:
            try:
                if float(actual) > float(estimate):
                    sentiment = "bullish"
                elif float(actual) < float(estimate):
                    sentiment = "bearish"
            except (ValueError, TypeError):
                pass

        reports.append(Report(
            source="finnhub",
            category="macro",
            title=event_name,
            content=content,
            sentiment=sentiment,
            relevance=0.9 if is_important else 0.6,
            timestamp=event.get("time", from_date),
            url="",
        ))

    logger.info("Fetched %d economic calendar events", len(reports))
    return reports


def fetch_market_news(limit: int = 10) -> list[Report]:
    """Fetch general market news from Finnhub."""
    if not FINNHUB_API_KEY:
        return []

    url = f"https://finnhub.io/api/v1/news?category=general&token={FINNHUB_API_KEY}"
    data = _http_get_json(url)
    if not data or not isinstance(data, list):
        return []

    reports = []
    for item in data[:limit]:
        headline = item.get("headline", "")
        summary = item.get("summary", "")[:500]
        detected = _detect_sentiment(headline + " " + summary)
        reports.append(Report(
            source="finnhub",
            category="institutional",
            title=headline,
            content=summary,
            sentiment=detected,
            relevance=0.6 if detected != "neutral" else 0.4,
            timestamp=datetime.fromtimestamp(
                item.get("datetime", 0), tz=timezone.utc
            ).isoformat() if item.get("datetime") else "",
            url=item.get("url", ""),
        ))

    logger.info("Fetched %d market news items", len(reports))
    return reports


def fetch_crypto_news(limit: int = 15) -> list[Report]:
    """Fetch crypto news from CryptoPanic API (free tier)."""
    if not CRYPTOPANIC_API_KEY:
        logger.info("CRYPTOPANIC_API_KEY not set — skipping crypto news")
        return []

    # Try v1 first, then free/v1 fallback
    for api_path in ["v1", "free/v1"]:
        url = f"https://cryptopanic.com/api/{api_path}/posts/?auth_token={CRYPTOPANIC_API_KEY}&public=true"
        data = _http_get_json(url)
        if data and "results" in data:
            break
    else:
        logger.warning("CryptoPanic API unavailable (all paths returned error)")
        return []

    reports = []
    for item in data["results"][:limit]:
        # CryptoPanic has sentiment votes
        votes = item.get("votes", {})
        positive = votes.get("positive", 0) + votes.get("liked", 0)
        negative = votes.get("negative", 0) + votes.get("disliked", 0)

        if positive > negative * 1.5:
            sentiment = "bullish"
        elif negative > positive * 1.5:
            sentiment = "bearish"
        else:
            sentiment = "neutral"

        # Relevance based on kind
        kind = item.get("kind", "news")
        relevance = {"news": 0.6, "media": 0.5, "analysis": 0.8}.get(kind, 0.5)

        reports.append(Report(
            source="cryptopanic",
            category="crypto",
            title=item.get("title", ""),
            content=item.get("title", ""),  # CryptoPanic free tier only gives titles
            sentiment=sentiment,
            relevance=relevance,
            timestamp=item.get("published_at", ""),
            url=item.get("url", ""),
        ))

    logger.info("Fetched %d crypto news items", len(reports))
    return reports


def fetch_crypto_fear_greed() -> list[Report]:
    """Fetch Fear & Greed Index history (7 days)."""
    url = "https://api.alternative.me/fng/?limit=7"
    data = _http_get_json(url)
    if not data or "data" not in data:
        return []

    latest = data["data"][0]
    value = int(latest.get("value", 50))

    if value <= 25:
        sentiment = "bearish"
        label = "極度恐懼"
    elif value <= 40:
        sentiment = "bearish"
        label = "恐懼"
    elif value <= 60:
        sentiment = "neutral"
        label = "中性"
    elif value <= 75:
        sentiment = "bullish"
        label = "貪婪"
    else:
        sentiment = "bullish"
        label = "極度貪婪"

    # Build 7-day trend
    values = [int(d.get("value", 50)) for d in data["data"]]
    trend = "上升" if values[0] > values[-1] else "下降" if values[0] < values[-1] else "持平"

    return [Report(
        source="alternative.me",
        category="sentiment",
        title=f"恐懼貪婪指數: {value} ({label})",
        content=f"當前: {value} ({label}) | 7日趨勢: {trend} | 7日值: {values}",
        sentiment=sentiment,
        relevance=0.8,
        timestamp=datetime.now(timezone.utc).isoformat(),
        url="https://alternative.me/crypto/fear-and-greed-index/",
    )]


# =============================================
# Collect All Sources
# =============================================

def collect_all() -> list[Report]:
    """Collect reports from all available sources."""
    all_reports: list[Report] = []

    # Macro
    all_reports.extend(fetch_economic_calendar())
    all_reports.extend(fetch_market_news())

    # Crypto
    all_reports.extend(fetch_crypto_news())
    all_reports.extend(fetch_crypto_fear_greed())

    # Finnhub crypto news (free tier — search for BTC/crypto keywords)
    if FINNHUB_API_KEY:
        url = f"https://finnhub.io/api/v1/news?category=crypto&token={FINNHUB_API_KEY}"
        data = _http_get_json(url)
        if data and isinstance(data, list):
            for item in data[:8]:
                all_reports.append(Report(
                    source="finnhub",
                    category="crypto",
                    title=item.get("headline", ""),
                    content=item.get("summary", "")[:300],
                    sentiment=_detect_sentiment(item.get("headline", "") + " " + item.get("summary", "")),
                    relevance=0.6,
                    timestamp=datetime.fromtimestamp(
                        item.get("datetime", 0), tz=timezone.utc
                    ).isoformat() if item.get("datetime") else "",
                    url=item.get("url", ""),
                ))
            logger.info("Fetched %d Finnhub crypto news", min(len(data), 8))

    # Deduplicate by title (case-insensitive)
    seen = set()
    unique = []
    for r in all_reports:
        key = r.title.lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(r)

    # Sort by relevance (highest first)
    unique.sort(key=lambda r: r.relevance, reverse=True)

    logger.info("Collected %d unique reports from %d raw", len(unique), len(all_reports))
    return unique


def save_reports(reports: list[Report]) -> Path:
    """Save collected reports to JSON."""
    DIGEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "count": len(reports),
        "reports": [asdict(r) for r in reports],
    }
    with open(DIGEST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("Saved %d reports to %s", len(reports), DIGEST_FILE)
    return DIGEST_FILE


# =============================================
# AI Digest Generation
# =============================================

def generate_digest(reports: list[Report]) -> str:
    """Generate Traditional Chinese digest summary using Claude API."""
    if not reports:
        return "📭 目前沒有可用的機構報告。"

    # Group by category
    groups: dict[str, list[Report]] = {}
    for r in reports:
        groups.setdefault(r.category, []).append(r)

    # Build prompt content
    sections = []
    for cat, items in groups.items():
        cat_label = {"macro": "宏觀經濟", "crypto": "加密市場", "institutional": "投行/金融",
                     "sentiment": "市場情緒"}.get(cat, cat)
        lines = []
        for item in items[:8]:  # Max 8 per category
            lines.append(f"- [{item.sentiment.upper()}] {item.title}: {item.content[:200]}")
        sections.append(f"## {cat_label}\n" + "\n".join(lines))

    prompt_content = "\n\n".join(sections)

    # Try Claude API
    if ANTHROPIC_API_KEY:
        try:
            digest = _call_claude_digest(prompt_content)
            if digest:
                return digest
        except Exception as e:
            logger.warning("Claude digest failed: %s — falling back to simple digest", e)

    # Fallback: simple Python digest
    return _simple_digest(groups)


def _call_claude_digest(content: str) -> str | None:
    """Call Claude Haiku for digest generation."""
    import json as _json

    url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
    }
    body = _json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1500,
        "messages": [{
            "role": "user",
            "content": (
                "你是一位專業的投資研究分析師。請將以下收集到的機構報告和市場新聞，"
                "整理成一份繁體中文精華摘要。\n\n"
                "格式要求：\n"
                "1. 分成 🏛️ 宏觀經濟、💰 金融市場、₿ 加密市場、📊 市場情緒 四個部分\n"
                "2. 每個部分列出 3-5 個最重要的要點\n"
                "3. 最後加一段「📌 綜合研判」，給出整體市場方向判斷\n"
                "4. 保持簡潔，每個要點一行\n\n"
                f"=== 原始報告 ===\n{content}"
            ),
        }],
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = _json.loads(resp.read())

    if result.get("content"):
        return result["content"][0].get("text", "")
    return None


def _simple_digest(groups: dict[str, list[Report]]) -> str:
    """Fallback: simple Python-based digest without AI."""
    lines = ["📋 *機構報告精華* (自動摘要)\n"]

    cat_labels = {
        "macro": "🏛️ 宏觀經濟",
        "institutional": "💰 金融市場",
        "crypto": "₿ 加密市場",
        "sentiment": "📊 市場情緒",
    }

    for cat, label in cat_labels.items():
        items = groups.get(cat, [])
        if not items:
            continue
        lines.append(f"\n*{label}*")
        for item in items[:5]:
            emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(item.sentiment, "⚪")
            lines.append(f"  {emoji} {item.title}")

    # Overall sentiment
    all_items = [r for items in groups.values() for r in items]
    bullish = sum(1 for r in all_items if r.sentiment == "bullish")
    bearish = sum(1 for r in all_items if r.sentiment == "bearish")
    total = len(all_items)

    if total > 0:
        lines.append(f"\n📌 *綜合研判*: {bullish}多/{bearish}空/{total - bullish - bearish}中性")
        if bullish > bearish * 1.5:
            lines.append("整體偏多，市場情緒樂觀")
        elif bearish > bullish * 1.5:
            lines.append("整體偏空，注意風險控制")
        else:
            lines.append("多空分歧，建議觀望為主")

    return "\n".join(lines)


def save_digest(digest: str) -> Path:
    """Save digest to markdown file."""
    SUMMARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    header = f"# 機構報告精華摘要\n\n*生成時間: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*\n\n"
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        f.write(header + digest)
    logger.info("Saved digest to %s", SUMMARY_FILE)
    return SUMMARY_FILE


# =============================================
# Public API
# =============================================

def run_collection_and_digest() -> tuple[list[Report], str]:
    """Full pipeline: collect → save → digest → save."""
    reports = collect_all()
    save_reports(reports)

    digest = generate_digest(reports)
    save_digest(digest)

    return reports, digest


def get_latest_digest() -> str:
    """Read the latest saved digest (for Telegram bot)."""
    if SUMMARY_FILE.exists():
        return SUMMARY_FILE.read_text(encoding="utf-8")
    return "尚未生成機構報告摘要。請先執行收集。"


# =============================================
# CLI Entry Point
# =============================================

if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    parser = argparse.ArgumentParser(description="Institutional Report Collector")
    parser.add_argument("--fetch", action="store_true", help="Only fetch reports")
    parser.add_argument("--digest", action="store_true", help="Only generate digest from saved reports")
    args = parser.parse_args()

    if args.digest:
        # Load existing reports
        if DIGEST_FILE.exists():
            with open(DIGEST_FILE) as f:
                data = json.load(f)
            reports = [Report(**r) for r in data.get("reports", [])]
            digest = generate_digest(reports)
            save_digest(digest)
            print(digest)
        else:
            print("No saved reports found. Run --fetch first.")
            sys.exit(1)
    elif args.fetch:
        reports = collect_all()
        save_reports(reports)
        print(f"Collected {len(reports)} reports → {DIGEST_FILE}")
    else:
        reports, digest = run_collection_and_digest()
        print(f"Collected {len(reports)} reports")
        print("---")
        print(digest)
