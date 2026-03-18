---
name: crypto-market-rank
description: |
  Crypto market rankings and leaderboards. Query trending tokens, top searched tokens, Binance Alpha tokens,
  tokenized stocks, social hype sentiment ranks, smart money inflow token rankings,
  top meme token rankings from Pulse launchpad, and top trader PnL leaderboards.
  Use this skill when users ask about token rankings, market trends, social buzz, meme rankings, breakout meme tokens, or top traders.
metadata:
  author: binance-web3-team
  version: "2.0"
source: https://github.com/binance/binance-skills-hub/tree/main/skills/binance-web3/crypto-market-rank
---

# Crypto Market Rank Skill

## Overview

| API | Function | Use Case |
|-----|----------|----------|
| Social Hype Leaderboard | Social buzz ranking | Sentiment analysis, social summaries |
| Unified Token Rank | Multi-type token rankings | Trending, Top Search, Alpha, Stock with filters |
| Smart Money Inflow Rank | Token rank by smart money buys | Discover tokens smart money is buying most |
| Meme Rank | Top meme tokens from Pulse launchpad | Find meme tokens most likely to break out |
| Address Pnl Rank | Top trader PnL leaderboard | Top PnL traders / KOL performance ranking |

## Use Cases

1. **Social Hype Analysis**: Discover tokens with highest social buzz and sentiment
2. **Trending Tokens**: View currently trending tokens (rankType=10)
3. **Top Searched**: See most searched tokens (rankType=11)
4. **Alpha Discovery**: Browse Binance Alpha picks (rankType=20)
5. **Stock Tokens**: View tokenized stocks (rankType=40)
6. **Smart Money Inflow**: Discover which tokens smart money is buying most
7. **Meme Rank**: Find top meme tokens from Pulse launchpad most likely to break out
8. **PnL Leaderboard**: View top-performing trader addresses, PnL, win rates
9. **Filtered Research**: Combine filters for targeted token or address screening

## Supported Chains

| Chain | chainId |
|-------|---------|
| BSC | 56 |
| Base | 8453 |
| Solana | CT_501 |

---

## API 1: Social Hype Leaderboard

### Method: GET

**URL**:
```
https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/social/hype/rank/leaderboard
```

**Request Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| chainId | string | Yes | Chain ID |
| sentiment | string | No | Filter: `All`, `Positive`, `Negative`, `Neutral` |
| targetLanguage | string | Yes | Translation target, e.g., `en`, `zh` |
| timeRange | number | Yes | Time range, `1` = 24 hours |
| socialLanguage | string | No | Content language, `ALL` for all |

**Headers**: `Accept-Encoding: identity`

**Example**:
```bash
curl 'https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/social/hype/rank/leaderboard?chainId=56&sentiment=All&socialLanguage=ALL&targetLanguage=en&timeRange=1' \
-H 'Accept-Encoding: identity' \
-H 'User-Agent: binance-web3/2.0 (Skill)'
```

**Response** (`data.leaderBoardList[]`):

| Field Path | Type | Description |
|------------|------|-------------|
| metaInfo.logo | string | Icon URL path (prefix `https://bin.bnbstatic.com`) |
| metaInfo.symbol | string | Token symbol |
| metaInfo.chainId | string | Chain ID |
| metaInfo.contractAddress | string | Contract address |
| metaInfo.tokenAge | number | Creation timestamp (ms) |
| marketInfo.marketCap | number | Market cap (USD) |
| marketInfo.priceChange | number | Price change (%) |
| socialHypeInfo.socialHype | number | Total social hype index |
| socialHypeInfo.sentiment | string | Positive / Negative / Neutral |
| socialHypeInfo.socialSummaryBrief | string | Brief social summary |
| socialHypeInfo.socialSummaryDetail | string | Detailed social summary |

---

## API 2: Unified Token Rank

### Method: POST (recommended) / GET

**URL**:
```
https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/unified/rank/list
```

**Headers**: `Content-Type: application/json`, `Accept-Encoding: identity`

### Rank Types

| rankType | Name | Description |
|----------|------|-------------|
| 10 | Trending | Hot trending tokens |
| 11 | Top Search | Most searched tokens |
| 20 | Alpha | Alpha tokens (Binance Alpha picks) |
| 40 | Stock | Tokenized stock tokens |

### Request Body (all fields optional)

**Core Parameters**:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| rankType | integer | 10 | Rank type |
| chainId | string | - | Chain ID: `1`, `56`, `8453`, `CT_501` |
| period | integer | 50 | Time: `10`=1m, `20`=5m, `30`=1h, `40`=4h, `50`=24h |
| sortBy | integer | 0 | Sort field (see Sort Options) |
| orderAsc | boolean | false | Ascending order |
| page | integer | 1 | Page number |
| size | integer | 200 | Page size (max 200) |

**Filter Parameters (Min/Max pairs)**:

| Filter | Type | Description |
|--------|------|-------------|
| percentChangeMin/Max | decimal | Price change range (%) |
| marketCapMin/Max | decimal | Market cap range (USD) |
| volumeMin/Max | decimal | Volume range (USD) |
| liquidityMin/Max | decimal | Liquidity range (USD) |
| holdersMin/Max | long | Holder count range |

**Advanced Filters**:

| Field | Type | Description |
|-------|------|-------------|
| keywords | string[] | Include symbols matching keywords |
| excludes | string[] | Exclude these symbols |
| socials | integer[] | Social filter: `0`=at_least_one, `1`=X, `2`=Telegram, `3`=Website |
| auditFilter | integer[] | Audit: `0`=not_renounced, `1`=freezable, `2`=mintable |

### Sort Options

| sortBy | Field |
|--------|-------|
| 0 | Default |
| 10 | Launch time |
| 20 | Liquidity |
| 30 | Holders |
| 40 | Market cap |
| 50 | Price change |
| 60 | Transaction count |
| 70 | Volume |
| 90 | Price |
| 100 | Unique traders |

### Example Request

```bash
curl -X POST 'https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/unified/rank/list' \
-H 'Content-Type: application/json' \
-H 'Accept-Encoding: identity' \
-H 'User-Agent: binance-web3/2.0 (Skill)' \
-d '{"rankType":10,"chainId":"1","period":50,"sortBy":70,"orderAsc":false,"page":1,"size":20}'
```

### Token Response Fields (`data.tokens[]`)

| Field | Type | Description |
|-------|------|-------------|
| chainId | string | Chain ID |
| contractAddress | string | Contract address |
| symbol | string | Token symbol |
| icon | string | Logo URL path (prefix `https://bin.bnbstatic.com`) |
| price | string | Current price (USD) |
| marketCap | string | Market cap |
| liquidity | string | Liquidity |
| holders | string | Holder count |
| launchTime | string | Launch timestamp (ms) |
| percentChange{1m,5m,1h,4h,24h} | string | Price change by period (%) |
| volume{1m,5m,1h,4h,24h} | string | Volume by period (USD) |
| auditInfo | object | Audit info (riskLevel, riskNum) |

---

## API 3: Smart Money Inflow Rank

### Method: POST

**URL**:
```
https://web3.binance.com/bapi/defi/v1/public/wallet-direct/tracker/wallet/token/inflow/rank/query
```

**Request Body**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| chainId | string | Yes | `56` (BSC), `CT_501` (Solana) |
| period | string | No | `5m`, `1h`, `4h`, `24h` |
| tagType | integer | Yes | Address tag type (always `2`) |

### Example

```bash
curl -X POST 'https://web3.binance.com/bapi/defi/v1/public/wallet-direct/tracker/wallet/token/inflow/rank/query' \
-H 'Content-Type: application/json' \
-H 'Accept-Encoding: identity' \
-H 'User-Agent: binance-web3/2.0 (Skill)' \
-d '{"chainId":"56","period":"24h","tagType":2}'
```

### Response (`data[]`)

| Field | Type | Description |
|-------|------|-------------|
| tokenName | string | Token name |
| ca | string | Contract address |
| price | string | Current price (USD) |
| marketCap | string | Market cap (USD) |
| inflow | number | Smart money net inflow (USD) |
| traders | integer | Smart money addresses trading |
| tokenRiskLevel | integer | Risk (-1=unknown, 1=low, 2=medium, 3=high) |

---

## API 4: Meme Rank

### Method: GET

**URL**:
```
https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/exclusive/rank/list
```

**Parameters**: `chainId` (required, e.g., `56`)

Returns top 100 meme tokens from Pulse platform ranked by breakout potential score.

### Response (`data.tokens[]`)

| Field | Type | Description |
|-------|------|-------------|
| symbol | string | Token symbol |
| rank | integer | Rank position |
| score | string | Algorithm score (higher = more likely to break out) |
| price | string | Current price (USD) |
| marketCap | string | Market cap (USD) |
| holders | string | Total holder count |

---

## API 5: Address PnL Rank

### Method: GET

**URL**:
```
https://web3.binance.com/bapi/defi/v1/public/wallet-direct/market/leaderboard/query
```

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| chainId | string | Yes | `56` (BSC), `CT_501` (Solana) |
| period | string | Yes | `7d`, `30d`, `90d` |
| tag | string | Yes | `ALL`, `KOL` |
| pageNo | integer | No | Page number |
| pageSize | integer | No | Max 25 |

### Response (`data.data[]`)

| Field | Type | Description |
|-------|------|-------------|
| address | string | Wallet address |
| realizedPnl | string | Realized PnL (USD) |
| winRate | string | Win rate |
| totalVolume | string | Total volume (USD) |
| topEarningTokens | array | Top profit tokens |

---

## Notes

1. Icon/logo URLs require prefix: `https://bin.bnbstatic.com` + path
2. All numeric fields in responses are strings -- parse when needed
3. Include `User-Agent: binance-web3/2.0 (Skill)` header
4. Unified Token Rank supports both GET and POST; POST recommended
