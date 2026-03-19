# All Money Back Me Home

Crypto futures auto-trading system + multi-market indicator monitoring.

## Core Features

- **Crypto Futures Trading**: Automated OKX USDT perpetual contract trading via Freqtrade
- **Multi-Market Monitoring**: Taiwan & US stock indicator observation (no auto-trading)
- **Guard Pipeline**: Declarative risk control checks before every order execution
- **Trading-as-Git**: Every trade is an immutable commit with full audit trail
- **MCP Server**: AI-assisted strategy analysis and market scanning

## Architecture

```
OKX Exchange ←→ Freqtrade (CCXT) ←→ Strategies ←→ Guard Pipeline
                                         ↑
                              Custom Indicators (Adam Projection, Squeeze)
                                         ↑
                              Market Monitor (TW/US stocks)
                                         ↑
                              MCP Server (Claude integration)
```

## Tech Stack

| Layer | Choice |
|-------|--------|
| Exchange | OKX (API v5, USDT perpetual futures) |
| Framework | Freqtrade (Python, futures mode) |
| API | CCXT (unified exchange interface) |
| Market Data | yfinance (TW/US stocks) + OKX API (crypto) |
| Language | Python 3.11+ |
| Notifications | Telegram Bot |

## Quick Start

```bash
# Clone
git clone https://github.com/LRYuChi/All-money-back-me-home.git
cd All-money-back-me-home

# Setup
cp .env.example .env
# Edit .env with your OKX API keys (Demo Trading mode)

pip install -e ".[dev]"

# Verify OKX connection
freqtrade test-pairlist -c config/freqtrade/config_dry.json

# Run backtest
freqtrade backtesting --strategy SMCTrend -c config/freqtrade/config_dry.json

# Start dry run (paper trading)
freqtrade trade --strategy SMCTrend -c config/freqtrade/config_dry.json
```

## Project Structure

```
├── config/              # Configuration files
├── strategies/          # Freqtrade trading strategies
├── indicators/          # Custom technical indicators
├── guards/              # Risk control guard pipeline
├── trading_log/         # Trade commit history & event log
├── market_monitor/      # TW/US stock indicator monitoring
├── mcp/                 # MCP Server for AI integration
├── data/                # Market data storage
├── tests/               # Test suite
└── notebooks/           # Analysis notebooks
```

## Risk Controls (Guard Pipeline)

Every order must pass all guards before execution:

1. **MaxPositionGuard** - Single asset max 30% of account
2. **MaxLeverageGuard** - Max 5x leverage
3. **CooldownGuard** - 15-min cooldown per symbol
4. **DailyLossGuard** - Max 5% daily loss
5. **ConsecutiveLossGuard** - Auto-pause after 5 consecutive losses

## References

- [ai-trader](https://github.com/whchien/ai-trader) - Strategy patterns, indicators, MCP
- [trump-code](https://github.com/sstklen/trump-code) - Signal pipeline, learning engine, circuit breaker
- [OpenAlice](https://github.com/TraderAlice/OpenAlice) - Trading-as-Git, Guard Pipeline, event log

## License

MIT
