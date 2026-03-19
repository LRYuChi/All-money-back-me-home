"""
Global Macro Confidence Engine - FREE Data Sources Reference
=============================================================
All endpoints, code examples, update frequencies, and limitations
for building a macro-driven crypto trading signal engine.
"""

# =============================================================================
# 1. ECONOMIC CALENDAR (FOMC, CPI, NFP dates)
# =============================================================================
# Source: Finnhub (free tier)
# Endpoint: GET https://finnhub.io/api/v1/calendar/economic
# Update: Real-time, events published weeks in advance
# Free tier: 60 API calls/minute, all economic calendar data included
# Reliability: HIGH - well-maintained, rarely down
#
# Alternative: Trading Economics API (paid, more comprehensive)
# Alternative: FinanceFlowAPI (updates every 5-10 min after announcements)

def fetch_economic_calendar():
    """Fetch upcoming FOMC, CPI, NFP and other macro events."""
    import requests

    # Option A: Finnhub (requires free API key from finnhub.io)
    FINNHUB_API_KEY = "YOUR_FINNHUB_KEY"
    url = "https://finnhub.io/api/v1/calendar/economic"
    params = {
        "from": "2026-03-17",
        "to": "2026-04-17",
        "token": FINNHUB_API_KEY,
    }
    resp = requests.get(url, params=params)
    events = resp.json().get("economicCalendar", [])
    # Filter for high-impact events
    high_impact = [e for e in events if e.get("impact") == 3]
    return high_impact

    # Option B: Hardcoded FOMC dates (always known in advance)
    # https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
    # FOMC_2026 = [
    #     "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    #     "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16"
    # ]


# =============================================================================
# 2. FRED API (Federal Reserve Economic Data)
# =============================================================================
# Source: FRED via fredapi Python library
# Endpoint: https://api.stlouisfed.org/fred/series/observations
# Update: Varies by series (daily/weekly/monthly/quarterly)
# Free tier: UNLIMITED requests with free API key
# Reliability: VERY HIGH - official Fed data, extremely stable
#
# Key Series IDs:
#   M2SL     - M2 Money Supply (monthly)
#   WM2NS    - M2 Money Supply (weekly, not seasonally adjusted)
#   DFF      - Federal Funds Rate (daily)
#   CPIAUCSL - CPI All Items (monthly)
#   UNRATE   - Unemployment Rate (monthly)
#   DGS10    - 10-Year Treasury Yield (daily)
#   DGS2     - 2-Year Treasury Yield (daily)
#   T10Y2Y   - 10Y-2Y Spread (daily)
#   DTWEXBGS - Trade Weighted Dollar Index (daily)
#   WALCL    - Fed Balance Sheet Total Assets (weekly)
#   RRPONTSYD - Reverse Repo (daily)

def fetch_fred_data():
    """Fetch macro indicators from FRED."""
    # pip install fredapi
    from fredapi import Fred

    fred = Fred(api_key="YOUR_FRED_API_KEY")  # free at https://fred.stlouisfed.org/docs/api/api_key.html

    # M2 Money Supply
    m2 = fred.get_series("WM2NS")

    # Federal Funds Rate
    fed_rate = fred.get_series("DFF")

    # 10-Year Treasury Yield
    dgs10 = fred.get_series("DGS10")

    # 2-Year Treasury Yield (for yield curve inversion signal)
    dgs2 = fred.get_series("DGS2")

    # Yield curve spread (negative = inversion = recession signal)
    spread = fred.get_series("T10Y2Y")

    # CPI (inflation)
    cpi = fred.get_series("CPIAUCSL")

    # Fed Balance Sheet
    fed_balance = fred.get_series("WALCL")

    # Reverse Repo (liquidity drain)
    rrp = fred.get_series("RRPONTSYD")

    return {
        "m2": m2,
        "fed_rate": fed_rate,
        "dgs10": dgs10,
        "dgs2": dgs2,
        "yield_spread": spread,
        "cpi": cpi,
        "fed_balance_sheet": fed_balance,
        "reverse_repo": rrp,
    }


# =============================================================================
# 3. CRYPTO FEAR & GREED INDEX
# =============================================================================
# Source: Alternative.me (FREE, no API key required)
# Endpoint: GET https://api.alternative.me/fng/
# Update: Daily (new value each day)
# Free tier: NO limits, no auth required
# Reliability: HIGH - running since 2018, widely used
#
# Scale: 0 = Extreme Fear, 100 = Extreme Greed
# Components: Volatility (25%), Volume (25%), Social Media (15%),
#             Surveys (15%), BTC Dominance (10%), Trends (10%)

def fetch_fear_greed_index():
    """Fetch Crypto Fear & Greed Index."""
    import requests

    # Option A: Alternative.me (no API key needed!)
    url = "https://api.alternative.me/fng/"
    params = {"limit": 30, "format": "json"}  # last 30 days
    resp = requests.get(url, params=params)
    data = resp.json()["data"]
    # Each entry: {"value": "73", "value_classification": "Greed", "timestamp": "..."}
    return data

    # Option B: CoinMarketCap (requires free API key)
    # CMC_API_KEY = "YOUR_CMC_KEY"
    # url = "https://pro-api.coinmarketcap.com/v3/fear-and-greed/historical"
    # headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
    # resp = requests.get(url, headers=headers)

    # Option C: CoinyBubble (free, no signup)
    # url = "https://api.coinybubble.com/fear-greed"
    # resp = requests.get(url)


# =============================================================================
# 4. DXY (US DOLLAR INDEX)
# =============================================================================
# Source: Yahoo Finance via yfinance
# Ticker: DX-Y.NYB
# Update: Real-time during market hours (delayed ~15 min on free tier)
# Free tier: Unlimited (unofficial API, may break)
# Reliability: MEDIUM - Yahoo can change/block; use as secondary
#
# Alternative: Calculate DXY manually from forex pairs:
#   DXY = 50.14348112 * (EURUSD^-0.576) * (USDJPY^0.136) *
#         (GBPUSD^-0.119) * (USDCAD^0.091) * (USDSEK^0.042) * (USDCHF^0.036)
#
# Alternative: FRED series DTWEXBGS (Trade Weighted Dollar, daily, very reliable)

def fetch_dxy():
    """Fetch US Dollar Index."""
    # pip install yfinance
    import yfinance as yf

    # Direct DXY
    dxy = yf.download("DX-Y.NYB", period="6mo", interval="1d")
    return dxy["Close"]

    # Alternative: FRED Trade Weighted Dollar (more reliable, daily)
    # from fredapi import Fred
    # fred = Fred(api_key="YOUR_KEY")
    # return fred.get_series("DTWEXBGS")


# =============================================================================
# 5. VIX (CBOE Volatility Index)
# =============================================================================
# Source: Yahoo Finance via yfinance
# Ticker: ^VIX
# Update: Real-time during market hours (delayed ~15 min)
# Free tier: Unlimited (unofficial API)
# Reliability: MEDIUM - same yfinance caveats as DXY
#
# Key levels: <15 = complacent, 15-20 = normal, 20-30 = elevated,
#             30-40 = high fear, >40 = extreme panic
#
# Alternative: FRED series VIXCLS (daily close, very reliable)

def fetch_vix():
    """Fetch VIX volatility index."""
    import yfinance as yf

    vix = yf.download("^VIX", period="1y", interval="1d")
    return vix["Close"]

    # More reliable alternative via FRED:
    # from fredapi import Fred
    # fred = Fred(api_key="YOUR_KEY")
    # return fred.get_series("VIXCLS")


# =============================================================================
# 6. GLOBAL M2 MONEY SUPPLY (Liquidity Proxy)
# =============================================================================
# Source: FRED for US M2 + manual aggregation for global
# Update: US M2 weekly (WM2NS) or monthly (M2SL)
# Free tier: Unlimited via FRED API
# Reliability: HIGH for US data; global aggregation requires manual work
#
# Global M2 = US M2 + ECB M2 + BOJ M2 + PBOC M2 (converted to USD)
# FRED Series: WM2NS (US), ECBM2 (Eurozone via ECB), MABMM301JPM189S (Japan)
# China M2: Available from PBOC / Trading Economics (scraping may be needed)
#
# Alternative: Bootleg_Macro (pip install bootleg-macro) aggregates global M2

def fetch_global_m2():
    """Fetch global M2 money supply (US + major central banks)."""
    from fredapi import Fred

    fred = Fred(api_key="YOUR_FRED_API_KEY")

    # US M2 (weekly)
    us_m2 = fred.get_series("WM2NS")  # billions USD

    # Eurozone M3 (closest equivalent, monthly)
    # FRED series: MABMM301EZM189S (or use ECB API directly)
    _eu_m2 = fred.get_series("MYAGM2EZM196N")  # noqa: F841

    # Japan M2 (monthly)
    _jp_m2 = fred.get_series("MABMM301JPM189S")  # noqa: F841

    # China M2 - not directly on FRED, use Trading Economics or manual
    # Approximate: China M2 ~$40T USD equivalent
    # Can scrape from: https://tradingeconomics.com/china/money-supply-m2

    # Simple US-only liquidity signal (still very useful)
    return us_m2

    # For full global M2, see:
    # https://github.com/HelloThereMatey/Bootleg_Macro
    # pip install bootleg-macro


# =============================================================================
# 7. BITCOIN DOMINANCE (BTC.D)
# =============================================================================
# Source: CoinGecko API (free tier)
# Endpoint: GET https://api.coingecko.com/api/v3/global
# Update: ~every 1-10 minutes
# Free tier: 30 calls/min, 10,000 calls/month (Demo plan, free)
# Reliability: HIGH - major provider, well-maintained
#
# BTC.D = BTC market cap / total crypto market cap
# High BTC.D (>60%) = risk-off (money flows to BTC safety)
# Low BTC.D (<40%) = alt season / risk-on

def fetch_btc_dominance():
    """Fetch Bitcoin dominance percentage."""
    import requests

    # CoinGecko /global endpoint (no API key needed for basic access)
    url = "https://api.coingecko.com/api/v3/global"
    resp = requests.get(url)
    data = resp.json()["data"]

    btc_dominance = data["market_cap_percentage"]["btc"]
    eth_dominance = data["market_cap_percentage"]["eth"]
    total_market_cap = data["total_market_cap"]["usd"]

    return {
        "btc_dominance": btc_dominance,      # e.g., 54.2 (percent)
        "eth_dominance": eth_dominance,
        "total_market_cap_usd": total_market_cap,
        "total_volume_24h": data["total_volume"]["usd"],
    }


# =============================================================================
# 8. STABLECOIN MARKET CAP (Flow Tracking)
# =============================================================================
# Source: DefiLlama API (FREE, no API key required)
# Endpoint: GET https://stablecoins.llama.fi/stablecoins
# Update: Near real-time (updates every few minutes)
# Free tier: NO limits, no auth, fully open
# Reliability: VERY HIGH - open-source, community-maintained, de facto standard
#
# Rising stablecoin mcap = money flowing INTO crypto (bullish)
# Falling stablecoin mcap = money flowing OUT of crypto (bearish)

def fetch_stablecoin_data():
    """Fetch stablecoin market cap data from DefiLlama."""
    import requests

    # All stablecoins overview
    url = "https://stablecoins.llama.fi/stablecoins?includePrices=true"
    resp = requests.get(url)
    data = resp.json()

    total_mcap = sum(
        s["circulating"]["peggedUSD"]
        for s in data["peggedAssets"]
        if "peggedUSD" in s.get("circulating", {})
    )

    # Top stablecoins breakdown
    top_stables = []
    for s in sorted(data["peggedAssets"],
                    key=lambda x: x.get("circulating", {}).get("peggedUSD", 0),
                    reverse=True)[:10]:
        top_stables.append({
            "name": s["name"],
            "symbol": s["symbol"],
            "mcap": s.get("circulating", {}).get("peggedUSD", 0),
        })

    # Historical total stablecoin mcap
    hist_url = "https://stablecoins.llama.fi/stablecoincharts/all?stablecoin=1"
    hist_resp = requests.get(hist_url)
    history = hist_resp.json()  # list of {date, totalCirculating, ...}

    return {
        "total_stablecoin_mcap": total_mcap,
        "top_stablecoins": top_stables,
        "history": history,
    }


# =============================================================================
# 9. US TREASURY YIELDS (10-Year, 2-Year)
# =============================================================================
# Source: FRED API (best option) or Yahoo Finance
# Endpoint: FRED series DGS10, DGS2
# Update: Daily (FRED), real-time (yfinance with ^TNX ticker)
# Free tier: Unlimited via FRED
# Reliability: VERY HIGH (FRED) / MEDIUM (yfinance)
#
# Key signals:
#   Rising yields = tightening, bearish for risk assets
#   Falling yields = easing expectations, bullish for crypto
#   Inverted curve (2Y > 10Y) = recession signal

def fetch_treasury_yields():
    """Fetch US Treasury yields."""
    # Option A: FRED (most reliable)
    from fredapi import Fred

    fred = Fred(api_key="YOUR_FRED_API_KEY")
    dgs10 = fred.get_series("DGS10")   # 10-Year
    dgs2 = fred.get_series("DGS2")     # 2-Year
    spread = fred.get_series("T10Y2Y")  # 10Y-2Y spread

    return {"10y": dgs10, "2y": dgs2, "spread": spread}

    # Option B: yfinance (real-time during market hours)
    # import yfinance as yf
    # tnx = yf.download("^TNX", period="1y")  # 10-Year yield
    # fvx = yf.download("^FVX", period="1y")  # 5-Year yield
    # tyx = yf.download("^TYX", period="1y")  # 30-Year yield

    # Option C: US Treasury Fiscal Data API (official, no key needed)
    # import requests
    # url = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/avg_interest_rates"
    # resp = requests.get(url, params={"sort": "-record_date", "page[size]": 10})


# =============================================================================
# 10. CRYPTO OPTIONS EXPIRY CALENDAR
# =============================================================================
# Source: Deribit API (free, no key for public data)
# Endpoint: GET https://www.deribit.com/api/v2/public/get_expirations
# Update: Real-time
# Free tier: Public endpoints are free, no auth needed
# Reliability: HIGH - Deribit is the largest crypto options exchange
#
# Key dates: Quarterly expiries (last Friday of Mar/Jun/Sep/Dec)
# These cause massive volatility as billions in options settle.
#
# Alternative: CoinGlass for visual data (https://www.coinglass.com/pro/options/OIExpiry)

def fetch_crypto_options_expiry():
    """Fetch crypto options expiry dates from Deribit."""
    import requests
    from datetime import datetime

    # Get all available expiry dates
    url = "https://www.deribit.com/api/v2/public/get_expirations"
    params = {"currency": "BTC", "kind": "option"}
    resp = requests.get(url, params=params)
    _expirations = resp.json()["result"]  # noqa: F841
    # Returns: {"future": [...], "option": ["21MAR26", "28MAR26", ...]}

    # Get all active option instruments with open interest
    instruments_url = "https://www.deribit.com/api/v2/public/get_instruments"
    params = {"currency": "BTC", "kind": "option", "expired": False}
    resp = requests.get(instruments_url, params=params)
    instruments = resp.json()["result"]

    # Group open interest by expiry date
    from collections import defaultdict
    expiry_oi = defaultdict(float)
    for inst in instruments:
        expiry_ts = inst["expiration_timestamp"] / 1000
        expiry_date = datetime.utcfromtimestamp(expiry_ts).strftime("%Y-%m-%d")
        expiry_oi[expiry_date] += inst.get("open_interest", 0)

    # Sort by open interest to find biggest upcoming expiries
    sorted_expiries = sorted(expiry_oi.items(), key=lambda x: x[1], reverse=True)

    return sorted_expiries

    # Alternative: Use CCXT library for unified exchange access
    # pip install ccxt
    # import ccxt
    # exchange = ccxt.deribit()
    # markets = exchange.load_markets()
    # options = {k: v for k, v in markets.items() if v["type"] == "option"}


# =============================================================================
# COMBINED: MACRO CONFIDENCE SCORE ENGINE
# =============================================================================

def calculate_macro_confidence():
    """
    Combine all macro signals into a single confidence score (-100 to +100).

    Positive = bullish macro environment for crypto
    Negative = bearish macro environment

    Weights and thresholds should be calibrated with backtesting.
    """
    score = 0

    # Each signal contributes a weighted component:
    #
    # SIGNAL                  | WEIGHT | BULLISH WHEN
    # ========================|========|=================================
    # Fear & Greed            |  10%   | < 25 (extreme fear = buy signal)
    # VIX                     |  10%   | < 20 (low volatility = risk-on)
    # DXY                     |  15%   | Falling (weak dollar = crypto up)
    # M2 Money Supply         |  15%   | Growing (more liquidity)
    # Fed Funds Rate          |  10%   | Falling / dovish trajectory
    # 10Y Yield               |  10%   | Falling (easing conditions)
    # Yield Curve Spread      |   5%   | Positive (no inversion)
    # BTC Dominance           |   5%   | Context-dependent
    # Stablecoin MCap         |  10%   | Growing (money entering crypto)
    # Fed Balance Sheet       |   5%   | Growing (QE = bullish)
    # Options Expiry Proximity|   5%   | Gamma squeeze risk near expiry
    #
    # TOTAL                   | 100%
    #

    # Implementation left as exercise - plug in the fetch functions above
    # and apply scoring logic based on current values vs thresholds.

    return score


# =============================================================================
# INSTALLATION & DEPENDENCIES
# =============================================================================
# pip install fredapi yfinance requests pandas ccxt
#
# API Keys needed (all free):
#   1. FRED API key:    https://fred.stlouisfed.org/docs/api/api_key.html
#   2. Finnhub API key: https://finnhub.io/register
#   3. CoinGecko Demo:  https://www.coingecko.com/en/api (optional, works without)
#
# NO API key needed:
#   - Alternative.me Fear & Greed
#   - DefiLlama stablecoin data
#   - Deribit public endpoints
#   - yfinance (VIX, DXY)
#   - US Treasury Fiscal Data API
