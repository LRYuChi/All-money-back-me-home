---
name: spot
description: Binance Spot request using the Binance API. Authentication requires API key and secret key. Supports testnet, mainnet, and demo.
metadata:
  version: 1.0.2
source: https://github.com/binance/binance-skills-hub/tree/main/skills/binance/spot
---

# Binance Spot Trading API

Place and manage spot trading orders on Binance via API key authentication, supporting mainnet and testnet.

## Endpoints Overview

The skill provides 60+ endpoints organized into categories:

### Market Data (No Authentication Required)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v3/ping` | GET | Test connectivity |
| `/api/v3/time` | GET | Server time |
| `/api/v3/exchangeInfo` | GET | Exchange information, trading rules |
| `/api/v3/depth` | GET | Order book depth |
| `/api/v3/trades` | GET | Recent trades |
| `/api/v3/historicalTrades` | GET | Historical trades |
| `/api/v3/aggTrades` | GET | Aggregate trades |
| `/api/v3/klines` | GET | Candlestick/kline data |
| `/api/v3/avgPrice` | GET | Current average price |
| `/api/v3/ticker/24hr` | GET | 24hr ticker price change |
| `/api/v3/ticker/price` | GET | Symbol price ticker |
| `/api/v3/ticker/bookTicker` | GET | Best price/qty on order book |

### Trading Operations (Authentication Required)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v3/order` | POST | New order |
| `/api/v3/order` | DELETE | Cancel order |
| `/api/v3/order` | GET | Query order |
| `/api/v3/openOrders` | GET | Current open orders |
| `/api/v3/openOrders` | DELETE | Cancel all open orders |
| `/api/v3/allOrders` | GET | All orders |
| `/api/v3/order/oco` | POST | New OCO order |
| `/api/v3/sor/order` | POST | Smart Order Router order |

### Account Management (Authentication Required)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v3/account` | GET | Account information and balances |
| `/api/v3/myTrades` | GET | Account trade list |
| `/api/v3/rateLimit/order` | GET | Current order count usage |

## Authentication

### Requirements

- **API Key**: Provided in `X-MBX-APIKEY` header
- **Secret Key**: Used for HMAC SHA256 request signing

### Environment URLs

| Environment | Base URL |
|-------------|----------|
| Mainnet | `https://api.binance.com` |
| Testnet | `https://testnet.binance.vision` |

### Security Practices

- **Never disclose** the location of the API key and secret file
- **Never send** credentials to any website other than Mainnet and Testnet
- When displaying credentials, **mask the secret key** showing only last 5 characters: `***...aws1`
- For mainnet transactions, **always confirm with the user** before proceeding by asking them to write 'CONFIRM'

### Request Signing

1. Build query string with `timestamp` parameter (Unix milliseconds)
2. Percent-encode parameters (RFC 3986, UTF-8)
3. Compute HMAC SHA256 signature of query string using secret key
4. Append `signature` parameter to request
5. Include `X-MBX-APIKEY` header

**Example**:
```bash
# Build the query string
QUERY="symbol=BTCUSDT&side=BUY&type=MARKET&quoteOrderQty=100&timestamp=$(date +%s000)"

# Sign it
SIGNATURE=$(echo -n "$QUERY" | openssl dgst -sha256 -hmac "$SECRET_KEY" | cut -d' ' -f2)

# Send request
curl -X POST "https://api.binance.com/api/v3/order?${QUERY}&signature=${SIGNATURE}" \
  -H "X-MBX-APIKEY: ${API_KEY}"
```

## Order Types

| Type | Description |
|------|-------------|
| MARKET | Execute at current market price |
| LIMIT | Execute at specified price or better |
| STOP_LOSS | Market order triggered at stop price |
| STOP_LOSS_LIMIT | Limit order triggered at stop price |
| TAKE_PROFIT | Market order triggered at profit target |
| TAKE_PROFIT_LIMIT | Limit order triggered at profit target |
| LIMIT_MAKER | Limit order that will be rejected if it would immediately match |

## New Order Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| symbol | STRING | Yes | Trading pair (e.g., BTCUSDT) |
| side | ENUM | Yes | `BUY` or `SELL` |
| type | ENUM | Yes | Order type (see above) |
| timeInForce | ENUM | Conditional | `GTC`, `IOC`, `FOK` (for LIMIT orders) |
| quantity | DECIMAL | Conditional | Order quantity |
| quoteOrderQty | DECIMAL | Conditional | Quote asset quantity (for MARKET orders) |
| price | DECIMAL | Conditional | Order price (for LIMIT orders) |
| stopPrice | DECIMAL | Conditional | Trigger price (for STOP/TAKE_PROFIT) |
| newClientOrderId | STRING | No | Must start with `agent-` prefix |
| recvWindow | LONG | No | Max 60,000 ms |

## Kline/Candlestick Intervals

`1s`, `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `8h`, `12h`, `1d`, `3d`, `1w`, `1M`

## Common Use Cases

### Get Current Price
```bash
curl 'https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT'
```

### Get Order Book
```bash
curl 'https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=10'
```

### Get Candlestick Data
```bash
curl 'https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=24'
```

### Place Market Buy Order
```bash
# Requires authentication
POST /api/v3/order
{
  "symbol": "BTCUSDT",
  "side": "BUY",
  "type": "MARKET",
  "quoteOrderQty": 100,
  "timestamp": <unix_ms>,
  "signature": <hmac_sha256>
}
```

### Place Limit Sell Order
```bash
# Requires authentication
POST /api/v3/order
{
  "symbol": "BTCUSDT",
  "side": "SELL",
  "type": "LIMIT",
  "timeInForce": "GTC",
  "quantity": 0.001,
  "price": 100000,
  "timestamp": <unix_ms>,
  "signature": <hmac_sha256>
}
```

## Special Notes

- `newClientOrderId` must start with `agent-` prefix; system auto-generates if omitted
- `recvWindow` maximum is 60,000 milliseconds; supports microsecond precision
- `timestamp` is required for all authenticated requests (Unix milliseconds)
- For mainnet trading, always ask user to write 'CONFIRM' before executing
