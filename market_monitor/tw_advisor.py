"""台股投顧報告處理器 — 解析、交叉比對、摘要生成。

支援：
- 文字報告解析（Claude AI 結構化提取）
- 圖片報告解析（Claude Vision）
- 與系統信號交叉比對（TWSE + 信心引擎）
- Supabase 持久化 + 本地 JSON

使用方式:
    from market_monitor.tw_advisor import TWAdvisorProcessor
    processor = TWAdvisorProcessor()
    result = processor.process_text("投顧報告文字...")
    telegram_msg = result["telegram_message"]
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, date
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
_REPORTS_DIR = _DATA_DIR / "reports"
_SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
_SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
_ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")


@dataclass
class AdvisorReport:
    """投顧報告結構化資料。"""

    source: str = ""
    report_date: str = ""
    market_view: str = "neutral"  # bullish / bearish / neutral
    summary: str = ""
    key_points: list = field(default_factory=list)
    stock_picks: list = field(default_factory=list)
    sector_views: list = field(default_factory=list)
    risk_warnings: list = field(default_factory=list)
    sentiment_score: float = 0.5
    raw_text: str = ""


PARSE_PROMPT = """你是台股投顧報告分析師。請從以下報告中提取結構化資訊。

報告內容：
{report_text}

請提取以下資訊（嚴格 JSON 格式，無其他文字）：
{{
    "source": "投顧/券商名稱（若無法辨識填「未知」）",
    "report_date": "YYYY-MM-DD（若無法辨識填今天日期）",
    "market_view": "bullish 或 bearish 或 neutral",
    "summary": "50字以內的核心觀點",
    "key_points": ["重點1(20字內)", "重點2", "重點3"],
    "stock_picks": [
        {{"code": "2330", "name": "台積電", "action": "買進/賣出/持有", "target_price": 1200, "reason": "原因"}}
    ],
    "sector_views": [
        {{"sector": "半導體", "view": "正面/負面/中性", "reason": "原因"}}
    ],
    "risk_warnings": ["風險1", "風險2"],
    "sentiment_score": 0.7
}}

規則：
- sentiment_score: 0=極度悲觀, 0.5=中性, 1=極度樂觀
- stock_picks 中的 code 必須是台股代碼（4位數字）
- 若資訊不明確，用合理推斷但在 summary 中標注「部分推斷」
- target_price 若無明確數字填 null
"""

VISION_PROMPT = """這是一張台股投顧報告的截圖。請辨識所有文字內容，然後按照以下格式提取結構化資訊。

請提取以下資訊（嚴格 JSON 格式）：
{
    "source": "投顧/券商名稱",
    "report_date": "YYYY-MM-DD",
    "market_view": "bullish/bearish/neutral",
    "summary": "50字以內核心觀點",
    "key_points": ["重點1", "重點2", "重點3"],
    "stock_picks": [{"code":"2330","name":"台積電","action":"買進","target_price":1200,"reason":"..."}],
    "sector_views": [{"sector":"半導體","view":"正面","reason":"..."}],
    "risk_warnings": ["風險1"],
    "sentiment_score": 0.7
}
"""


class TWAdvisorProcessor:
    """台股投顧報告處理器。"""

    def process_text(self, raw_text: str) -> dict:
        """解析文字格式的投顧報告。"""
        report = self._parse_with_claude(raw_text)
        report.raw_text = raw_text
        cross_ref = self._cross_reference(report)
        self._save_local(report)
        self._save_supabase(report, cross_ref)
        msg = self._format_telegram(report, cross_ref)
        return {
            "report": asdict(report),
            "cross_reference": cross_ref,
            "telegram_message": msg,
        }

    def process_image(self, image_bytes: bytes) -> dict:
        """用 Claude Vision 解析報告截圖。"""
        report = self._parse_image_with_claude(image_bytes)
        cross_ref = self._cross_reference(report)
        self._save_local(report)
        self._save_supabase(report, cross_ref)
        msg = self._format_telegram(report, cross_ref)
        return {
            "report": asdict(report),
            "cross_reference": cross_ref,
            "telegram_message": msg,
        }

    # ------------------------------------------------------------------
    # Claude API 解析
    # ------------------------------------------------------------------

    def _parse_with_claude(self, text: str) -> AdvisorReport:
        """用 Claude 解析文字報告。"""
        prompt = PARSE_PROMPT.format(
            report_text=text[:5000]  # 限制長度避免浪費 token
        )
        raw = self._call_claude(prompt)
        return self._parse_json_response(raw, text)

    def _parse_image_with_claude(self, image_bytes: bytes) -> AdvisorReport:
        """用 Claude Vision 解析圖片報告。"""
        if not _ANTHROPIC_API_KEY:
            logger.warning("ANTHROPIC_API_KEY 未設定，無法解析圖片")
            return AdvisorReport(summary="無法解析：API Key 未設定")

        b64 = base64.b64encode(image_bytes).decode("utf-8")
        # 偵測圖片格式
        media_type = "image/jpeg"
        if image_bytes[:4] == b"\x89PNG":
            media_type = "image/png"

        try:
            url = f"{_ANTHROPIC_BASE_URL}/v1/messages"
            payload = {
                "model": "claude-sonnet-4-5",
                "max_tokens": 2000,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64,
                                },
                            },
                            {"type": "text", "text": VISION_PROMPT},
                        ],
                    }
                ],
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": _ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
                raw = result["content"][0]["text"]
                return self._parse_json_response(raw, f"[圖片報告] {raw[:200]}")
        except Exception as e:
            logger.error("Claude Vision 解析失敗: %s", e)
            return AdvisorReport(summary=f"圖片解析失敗: {e}")

    def _call_claude(self, prompt: str, model: str = "claude-haiku-4-5") -> str:
        """呼叫 Claude API（預設使用 Haiku 以節省成本）。"""
        if not _ANTHROPIC_API_KEY:
            logger.warning("ANTHROPIC_API_KEY 未設定")
            return ""
        try:
            url = f"{_ANTHROPIC_BASE_URL}/v1/messages"
            payload = {
                "model": model,
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": _ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                },
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                result = json.loads(resp.read())
                return result["content"][0]["text"]
        except Exception as e:
            logger.error("Claude API 失敗: %s", e)
            return ""

    def _parse_json_response(
        self, raw: str, original_text: str = ""
    ) -> AdvisorReport:
        """從 Claude 回覆中提取 JSON。"""
        try:
            # 找出回應中的 JSON 物件
            text = raw.strip()
            if "{" in text:
                start = text.index("{")
                depth = 0
                for i, c in enumerate(text[start:], start):
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                    if depth == 0:
                        data = json.loads(text[start : i + 1])
                        return AdvisorReport(
                            source=data.get("source", "未知"),
                            report_date=data.get(
                                "report_date", date.today().isoformat()
                            ),
                            market_view=data.get("market_view", "neutral"),
                            summary=data.get("summary", ""),
                            key_points=data.get("key_points", []),
                            stock_picks=data.get("stock_picks", []),
                            sector_views=data.get("sector_views", []),
                            risk_warnings=data.get("risk_warnings", []),
                            sentiment_score=float(
                                data.get("sentiment_score", 0.5)
                            ),
                            raw_text=original_text,
                        )
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("JSON 解析失敗: %s", e)

        return AdvisorReport(summary="解析失敗", raw_text=original_text)

    # ------------------------------------------------------------------
    # 交叉比對
    # ------------------------------------------------------------------

    def _cross_reference(self, report: AdvisorReport) -> dict:
        """與系統信號交叉比對（信心引擎 + TWSE 資料）。"""
        result: dict = {
            "agreements": [],
            "conflicts": [],
            "unique_insights": [],
        }

        # 1. 大盤觀點 vs 信心引擎
        self._cross_ref_confidence(report, result)

        # 2. 個股推薦 vs TWSE 即時行情
        self._cross_ref_stock_picks(report, result)

        # 3. 產業觀點 vs 產業指數表現
        self._cross_ref_sectors(report, result)

        return result

    def _cross_ref_confidence(
        self, report: AdvisorReport, result: dict
    ) -> None:
        """比對大盤觀點與信心引擎。"""
        try:
            from market_monitor.confidence_engine import GlobalConfidenceEngine

            engine = GlobalConfidenceEngine()
            conf = engine.calculate()
            regime = conf.get("regime", "UNKNOWN")
            score = conf.get("score", 0.5)

            bullish_regimes = {"NORMAL", "AGGRESSIVE"}
            bearish_regimes = {"DEFENSIVE", "HIBERNATE"}

            if report.market_view == "bullish":
                if regime in bullish_regimes:
                    result["agreements"].append(
                        f"投顧看多 ↔ 信心引擎 {regime}（一致 ✅）"
                    )
                elif regime in bearish_regimes:
                    result["conflicts"].append(
                        f"⚠️ 投顧看多 但信心引擎 {regime}（信心={score:.2f}）"
                    )
            elif report.market_view == "bearish":
                if regime in bearish_regimes:
                    result["agreements"].append(
                        f"投顧看空 ↔ 信心引擎 {regime}（一致 ✅）"
                    )
                elif regime in bullish_regimes:
                    result["conflicts"].append(
                        f"⚠️ 投顧看空 但信心引擎 {regime}（信心={score:.2f}）"
                    )
        except Exception as e:
            logger.debug("信心引擎比對跳過: %s", e)

    def _cross_ref_stock_picks(
        self, report: AdvisorReport, result: dict
    ) -> None:
        """比對個股推薦與 TWSE 即時行情。"""
        try:
            from market_monitor.fetchers.twse_openapi import TWSEOpenAPIClient

            twse = TWSEOpenAPIClient()
            for pick in report.stock_picks[:5]:  # 最多 5 檔避免過多請求
                code = str(pick.get("code", ""))
                name = pick.get("name", code)
                action = pick.get("action", "")
                target = pick.get("target_price")

                try:
                    quote = twse.get_stock_quote(code)
                    if quote and quote.get("close"):
                        close = float(quote["close"])
                        # 目標價位空間
                        if target and target > 0:
                            upside = (target - close) / close * 100
                            result["unique_insights"].append(
                                f"{name}({code}): 現價 {close:.0f}"
                                f" → 目標 {target:.0f} ({upside:+.1f}%)"
                            )
                        # 本益比檢查
                        fund = twse.get_stock_fundamentals(code)
                        if fund and fund.get("pe_ratio"):
                            pe = float(fund["pe_ratio"])
                            if pe > 30 and action == "買進":
                                result["conflicts"].append(
                                    f"⚠️ {name} PE={pe:.1f} 偏高"
                                    f" 但投顧建議買進"
                                )
                except Exception:
                    pass
        except ImportError:
            logger.debug("TWSE 模組不可用")

    def _cross_ref_sectors(
        self, report: AdvisorReport, result: dict
    ) -> None:
        """比對產業觀點與產業指數表現。"""
        try:
            from market_monitor.fetchers.twse_openapi import TWSEOpenAPIClient

            twse = TWSEOpenAPIClient()
            sectors = twse.get_sector_indices()
            if sectors:
                sector_map = {
                    s.get("name", ""): s
                    for s in sectors
                    if isinstance(s, dict)
                }
                for sv in report.sector_views:
                    sector_name = sv.get("sector", "")
                    # 模糊比對產業名稱
                    matched = None
                    for sn, sd in sector_map.items():
                        if sector_name in sn or sn in sector_name:
                            matched = sd
                            break
                    if matched:
                        change = matched.get("change_pct", 0)
                        result["unique_insights"].append(
                            f"{sector_name}: 投顧{sv.get('view', '?')}"
                            f" | 指數 {change:+.1f}%"
                        )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Telegram 訊息格式化
    # ------------------------------------------------------------------

    def _format_telegram(
        self, report: AdvisorReport, cross_ref: dict
    ) -> str:
        """格式化 Telegram 回覆訊息。"""
        view_map = {
            "bullish": "🟢 看多",
            "bearish": "🔴 看空",
            "neutral": "🟡 中性",
        }
        view_zh = view_map.get(report.market_view, "🟡 中性")

        msg = (
            f"📰 *投顧報告摘要*\n"
            f"━━━━━━━━━━━━━━\n"
            f"來源: {report.source} | 日期: {report.report_date}\n"
            f"觀點: {view_zh}\n"
            f"情緒: {report.sentiment_score:.0%}\n"
        )

        # 重點摘要
        if report.key_points:
            msg += "\n📌 *重點摘要*\n"
            for i, kp in enumerate(report.key_points[:5], 1):
                msg += f"{i}. {kp}\n"

        # 推薦個股
        if report.stock_picks:
            msg += "\n🎯 *推薦個股*\n"
            for pick in report.stock_picks[:5]:
                target_str = (
                    f" 目標 {pick['target_price']}"
                    if pick.get("target_price")
                    else ""
                )
                msg += (
                    f"• {pick.get('code', '')} {pick.get('name', '')}"
                    f" — {pick.get('action', '')}{target_str}\n"
                )

        # 交叉比對結果
        all_refs = (
            cross_ref.get("agreements", [])
            + cross_ref.get("conflicts", [])
            + cross_ref.get("unique_insights", [])
        )
        if all_refs:
            msg += "\n📊 *交叉比對*\n"
            for ref in all_refs[:6]:
                msg += f"  {ref}\n"

        # 風險提示
        if report.risk_warnings:
            msg += "\n⚡ *風險提示*\n"
            for rw in report.risk_warnings[:3]:
                msg += f"- {rw}\n"

        return msg

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _save_local(self, report: AdvisorReport) -> None:
        """存入本地 JSON（最新檔 + 歸檔）。"""
        try:
            _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            # 最新版（供信心引擎讀取）
            latest_path = _REPORTS_DIR / "tw_advisor_latest.json"
            with open(latest_path, "w", encoding="utf-8") as f:
                json.dump(
                    asdict(report), f, ensure_ascii=False, indent=2, default=str
                )
            # 依日期歸檔
            report_date = report.report_date or date.today().isoformat()
            archive_path = _REPORTS_DIR / f"tw_advisor_{report_date}.json"
            with open(archive_path, "w", encoding="utf-8") as f:
                json.dump(
                    asdict(report), f, ensure_ascii=False, indent=2, default=str
                )
            logger.info("投顧報告已存檔: %s", archive_path)
        except Exception as e:
            logger.warning("本地存檔失敗: %s", e)

    def _save_supabase(self, report: AdvisorReport, cross_ref: dict) -> None:
        """存入 Supabase advisor_reports 表。"""
        if not _SUPABASE_URL or not _SUPABASE_KEY:
            return
        try:
            url = f"{_SUPABASE_URL}/rest/v1/advisor_reports"
            payload = {
                "source": report.source,
                "report_date": report.report_date or date.today().isoformat(),
                "market_view": report.market_view,
                "summary": report.summary,
                "key_points": json.dumps(
                    report.key_points, ensure_ascii=False
                ),
                "stock_picks": json.dumps(
                    report.stock_picks, ensure_ascii=False
                ),
                "sector_views": json.dumps(
                    report.sector_views, ensure_ascii=False
                ),
                "risk_warnings": json.dumps(
                    report.risk_warnings, ensure_ascii=False
                ),
                "sentiment_score": report.sentiment_score,
                "cross_reference": json.dumps(cross_ref, ensure_ascii=False),
                "raw_text": report.raw_text[:10000],
            }
            data = json.dumps(payload, default=str).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "apikey": _SUPABASE_KEY,
                    "Authorization": f"Bearer {_SUPABASE_KEY}",
                    "Prefer": "return=minimal",
                },
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status in (200, 201):
                    logger.info("投顧報告已存入 Supabase")
        except Exception as e:
            logger.debug("Supabase 存入跳過: %s", e)
