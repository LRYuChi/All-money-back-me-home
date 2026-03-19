# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Crypto futures auto-trading system (OKX via Freqtrade) with multi-market indicator monitoring (TW/US stocks, macro). Includes a Next.js dashboard, FastAPI backend, MCP server for AI-assisted analysis, and a confidence engine that aggregates macro/sentiment/capital/haven signals into a 0-1 score driving position sizing.

## Commands

### Python (trading engine, strategies, guards, market monitor)
```bash
pip install -e ".[dev]"              # Install with dev deps
pytest                                # Run all tests
pytest tests/test_guards.py           # Run single test file
pytest -k "test_cooldown"             # Run single test by name
ruff check .                          # Lint Python
ruff check . --fix                    # Auto-fix lint issues
```

### Freqtrade
```bash
freqtrade backtesting --strategy SMCTrend -c config/freqtrade/config_dry.json
freqtrade trade --strategy SMCTrend -c config/freqtrade/config_dry.json   # Dry run
freqtrade test-pairlist -c config/freqtrade/config_dry.json               # Verify exchange connection
```

### Turborepo monorepo (web + api apps)
```bash
npm run dev                           # Start all apps (web:3000, api:8000)
npm run dev:web                       # Start Next.js dashboard only
npm run dev:api                       # Start FastAPI only (uvicorn --reload)
npm run build                         # Build all
npm run lint                          # Lint all
```

### API tests
```bash
cd apps/api && pytest                 # API-specific tests
```

### Docker
```bash
docker compose up                     # Dev: web + api
docker compose -f docker-compose.prod.yml up   # Prod: nginx + web + api
```

### Standalone tools
```bash
python -m market_monitor.pipeline     # Run market scan + Telegram report
python -m market_monitor.confidence_engine   # Run confidence engine dashboard
python -m mcp_server.server           # Start MCP server
```

## Architecture

### Data Flow
```
OKX Exchange ←→ Freqtrade (CCXT) ←→ Strategies ←→ Guard Pipeline → Trading Log (git commits)
                                          ↑
                               Confidence Engine (0-1 score → regime → position/leverage guidance)
                                          ↑
                   Macro + Sentiment + Capital Flow + Haven/Inflation sandboxes
                                          ↑
                   FRED, yfinance, alternative.me, CoinGecko, DefiLlama (all free APIs)
```

### Key Subsystems

- **strategies/**: Freqtrade strategy classes. `SMCTrend` is the sole active strategy, inherits `StrategyMixin` from `base_mixin.py` for shared risk params and logging. Hyperopt params in `smc_trend.json`.

- **guards/**: Declarative risk control pipeline. `guards/base.py` defines `GuardPipeline` + `Guard` base class. `guards/guards.py` has concrete guards. `guards/pipeline.py` has `create_default_pipeline()` factory. Every order must pass all guards.

- **market_monitor/**: Market data pipeline. `pipeline.py` fetches TW/US/crypto via yfinance, calculates RSI/MA signals, generates Traditional Chinese reports. `confidence_engine.py` is the 4-sandbox weighted confidence scorer (Macro 35%, Sentiment 30%, Capital 20%, Haven 15%) with event calendar overlay (FOMC/CPI dates). `telegram_zh.py` sends reports to Telegram.

- **mcp_server/**: FastMCP server exposing tools to Claude: `market_scan`, `confidence_score`, `trading_status`, `run_backtest`, `strategy_info`. Run as `python -m mcp_server.server`.

- **apps/web/**: Next.js 14 dashboard (React 18, Tailwind, Zustand, Supabase SSR, lightweight-charts). Pages: `/` (dashboard with confidence/macro panels), `/market/{tw,us,crypto}`, `/trades`, `/backtest`, `/symbol/[market]/[symbol]`.

- **apps/api/**: FastAPI backend. Routers: `analysis`, `market_data`, `strategy`, `dashboard`. Connects to Supabase for persistence. Entry: `apps/api/src/main.py`.

- **trading_log/**: Git-based trade audit trail — every trade becomes an immutable commit via `trading_git.py`.

- **supabase/**: Supabase config + migrations + seed data.

### Monorepo Structure

Turborepo manages `apps/web` (Next.js) and `apps/api` (FastAPI). Python root package (`pyproject.toml`) manages the trading engine, strategies, and market monitor.

## Agent Skills

Curated skills in `.skills/` directory. Reference them for best practices on: Supabase/PostgreSQL, Next.js, React, FastAPI/Pydantic, Binance crypto trading, Stripe integration.

## Key Conventions

- Reports and UI strings are in **Traditional Chinese** (繁體中文)
- Exchange: OKX USDT perpetual futures (not Binance for trading, despite skill files)
- Config files: `config/freqtrade/config_dry.json` (main), `config_secrets.json` (API keys, gitignored)
- Python target: 3.11+, line length 120 (ruff)
- Async test mode: `asyncio_mode = "auto"` in pytest
