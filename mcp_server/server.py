"""MCP Server for All-Money-Back-Me-Home trading system.

Exposes trading tools to Claude via Model Context Protocol:
- market_scan: Run market indicator scan
- confidence_score: Get current global confidence score
- trading_status: Check Freqtrade bot status
- run_backtest: Execute a backtest
- strategy_info: Get strategy parameters and performance

Usage:
    python -m mcp.server
    # Or add to Claude Code MCP config
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from fastmcp import FastMCP
except ImportError:
    print("fastmcp not installed. Run: pip install fastmcp")
    sys.exit(1)

mcp = FastMCP("all-money-back-me-home")

# Freqtrade API config
FT_API = os.environ.get("FT_API_URL", "http://127.0.0.1:8080/api/v1")
FT_AUTH = os.environ.get("FT_AUTH", "freqtrade:freqtrade")


def _ft_api(endpoint: str) -> dict | None:
    """Call Freqtrade REST API."""
    try:
        import base64
        auth = base64.b64encode(FT_AUTH.encode()).decode()
        req = urllib.request.Request(
            f"{FT_API}/{endpoint}",
            headers={"Authorization": f"Basic {auth}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


@mcp.tool()
def market_scan() -> str:
    """掃描全球市場指標：美股、台股、宏觀（VIX/黃金/原油）、加密貨幣。
    回傳包含 RSI、MA200 趨勢、超買超賣警示的完整報告。"""
    try:
        from market_monitor.pipeline import fetch_market_data, generate_report
        data = fetch_market_data()
        if data:
            return generate_report(data)
        return "無法取得市場數據"
    except Exception as e:
        return f"市場掃描失敗: {e}"


@mcp.tool()
def confidence_score() -> str:
    """取得當前全局信心引擎分數（0.0-1.0）。
    包含四個沙盒的個別分數和市場狀態判斷。"""
    try:
        os.environ.setdefault("FRED_API_KEY", os.getenv("FRED_API_KEY", ""))
        from market_monitor.confidence_engine import GlobalConfidenceEngine
        engine = GlobalConfidenceEngine()
        result = engine.calculate()

        lines = [
            f"信心分數: {result['score']:.2f} / 1.00",
            f"狀態: {result['regime']}",
            f"事件覆蓋: ×{result['event_multiplier']}",
            "",
            "沙盒分數:",
        ]
        for name, val in result["sandboxes"].items():
            lines.append(f"  {name}: {val:.2f}")

        lines.append("")
        lines.append("因子明細:")
        for sandbox, factors in result["factors"].items():
            for k, v in factors.items():
                indicator = "↑" if v > 0.55 else "↓" if v < 0.45 else "→"
                lines.append(f"  {k}: {v:.2f} {indicator}")

        g = result["guidance"]
        lines.extend([
            "",
            f"建議: 倉位 {g['position_pct']}%, 槓桿 {g['leverage']}x",
        ])
        return "\n".join(lines)
    except Exception as e:
        return f"信心引擎錯誤: {e}"


@mcp.tool()
def trading_status() -> str:
    """查詢 Freqtrade 模擬交易狀態：持倉、損益、餘額。"""
    profit = _ft_api("profit")
    status = _ft_api("status")
    balance = _ft_api("balance")

    if not profit:
        return "交易系統未連線（Freqtrade 未運行或 API 無法連接）"

    lines = ["交易系統狀態:"]

    # Profit
    lines.append(f"  總損益: {profit.get('profit_all_coin', 0):.2f} USDT "
                 f"({profit.get('profit_all_percent', 0):.2f}%)")
    lines.append(f"  已關閉交易: {profit.get('closed_trade_count', 0)}")
    lines.append(f"  勝/敗: {profit.get('winning_trades', 0)}/{profit.get('losing_trades', 0)}")

    # Open trades
    if status:
        lines.append(f"  持倉中: {len(status)} 筆")
        for t in status:
            lines.append(f"    {t.get('pair', '?')} {t.get('trade_direction', '?')} "
                         f"profit={t.get('profit_pct', 0):.2f}%")
    else:
        lines.append("  持倉中: 0 筆")

    # Balance
    if balance:
        for b in balance.get("currencies", []):
            if b.get("currency") == "USDT":
                lines.append(f"  餘額: {b.get('balance', 0):.2f} USDT")

    return "\n".join(lines)


@mcp.tool()
def run_backtest(strategy: str = "SMCTrend", timerange: str = "20240317-20260317") -> str:
    """執行策略回測。
    Args:
        strategy: 策略名稱（SMCTrend, TAHZANCrypto, AdaptiveRSI, ATRTrend）
        timerange: 回測區間（格式 YYYYMMDD-YYYYMMDD）
    """
    try:
        cmd = [
            str(PROJECT_ROOT / ".venv" / "bin" / "freqtrade"),
            "backtesting",
            "--strategy", strategy,
            "--timeframe", "1h",
            "--timerange", timerange,
            "-c", str(PROJECT_ROOT / "config/freqtrade/config_dry.json"),
            "-c", str(PROJECT_ROOT / "config/freqtrade/config_secrets.json"),
            "--strategy-path", str(PROJECT_ROOT / "strategies"),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(PROJECT_ROOT))
        output = result.stdout + result.stderr

        # Extract key metrics
        lines = []
        for line in output.split("\n"):
            if any(k in line for k in [
                "Total profit", "Sharpe", "Profit factor", "Absolute drawdown",
                "Total/Daily", "Win", "STRATEGY SUMMARY", strategy
            ]):
                lines.append(line.strip())

        return "\n".join(lines[-15:]) if lines else f"回測完成但無法解析結果\n{output[-500:]}"
    except subprocess.TimeoutExpired:
        return "回測超時（>5分鐘）"
    except Exception as e:
        return f"回測失敗: {e}"


@mcp.tool()
def strategy_info() -> str:
    """列出所有可用策略及其當前參數設定。"""
    strategies = {
        "SMCTrend": "SMC + 亞當理論 + 信心引擎 + 金字塔加碼（主力策略）",
        "TAHZANCrypto": "TAHZAN v5.7 環境感知動能獵殺系統",
        "AdaptiveRSI": "自適應 RSI（波動率動態週期）",
        "ATRTrend": "ATR Keltner Channel 趨勢策略",
    }

    lines = ["可用策略:"]
    for name, desc in strategies.items():
        lines.append(f"  {name}: {desc}")

    # Check for hyperopt params
    params_dir = PROJECT_ROOT / "strategies"
    for name in strategies:
        params_file = params_dir / f"{name}.json"
        if params_file.exists():
            with open(params_file) as f:
                params = json.load(f)
            lines.append(f"\n  {name} 優化參數:")
            for space, vals in params.get("params", {}).items():
                for k, v in vals.items():
                    lines.append(f"    {k}: {v}")

    # WFO results
    wfo_path = PROJECT_ROOT / "data" / "reports" / "wfo_smc_results.json"
    if wfo_path.exists():
        with open(wfo_path) as f:
            wfo = json.load(f)
        lines.extend([
            "",
            "SMCTrend WFO 結果:",
            f"  Avg OOS Profit: {wfo.get('avg_oos', 0):.2f}%",
            f"  WFO Efficiency: {wfo.get('wfo_efficiency', 0):.2f}",
            f"  Robust: {wfo.get('robust_count', 0)}/8",
            f"  Aggregate OOS: {wfo.get('aggregate_oos', 0):.2f}%",
        ])

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
