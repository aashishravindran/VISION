"""Financial Modeling Prep client.

FMP's `/api/v3/stock-screener` endpoint does the entire fundamental-filter
loop server-side in ONE request — what we currently do via 50-500 per-ticker
Tiingo calls. Free tier: 250 requests/day, no rate-limit-per-minute hassle.

Endpoints used:
- /api/v3/stock-screener   — the big win, server-side filter
- /api/v3/quote/{tickers}  — batch quote (up to 100 tickers in one call)
- /api/v3/profile/{ticker} — company profile (single, fallback)

Sign up at https://site.financialmodelingprep.com/developer/docs for a free
API key. We surface clear errors if FMP_API_KEY is missing or rate-limited
so the screener can fall back to Tiingo without crashing.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from vision import cache

FMP_BASE = "https://financialmodelingprep.com/api/v3"


class FMPError(RuntimeError):
    pass


class FMPNoKeyError(FMPError):
    """FMP_API_KEY not set — caller should fall back to another source."""


class FMPRateLimitError(FMPError):
    """FMP returned 429 — quota hit."""


def _key() -> str:
    k = os.environ.get("FMP_API_KEY")
    if not k:
        raise FMPNoKeyError("FMP_API_KEY not set in environment")
    return k


def _client() -> httpx.Client:
    return httpx.Client(base_url=FMP_BASE, timeout=20.0)


def screen(
    *,
    market_cap_min: float | None = None,
    market_cap_max: float | None = None,
    pe_min: float | None = None,
    pe_max: float | None = None,
    beta_min: float | None = None,
    beta_max: float | None = None,
    dividend_yield_min: float | None = None,
    sector: str | None = None,
    industry: str | None = None,
    exchange: str | None = "nasdaq,nyse",
    is_etf: bool | None = False,
    is_actively_trading: bool = True,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Server-side stock screener.

    Returns a list of dicts shaped like:
        {
          "symbol": "AAPL",
          "companyName": "Apple Inc.",
          "marketCap": 3000000000000,
          "sector": "Technology",
          "industry": "Consumer Electronics",
          "beta": 1.25,
          "price": 270.0,
          "lastAnnualDividend": 1.0,
          "volume": 50000000,
          "exchange": "NASDAQ Global Select",
          "exchangeShortName": "NASDAQ",
          "country": "US",
          "isEtf": false,
          "isFund": false,
          "isActivelyTrading": true,
        }

    Cached for 6h on identical filter sets — typical screener usage burns
    1-3 unique filter combos per session, so cache hit rate is high.
    """
    params: dict[str, Any] = {"limit": str(min(limit, 1000))}
    if market_cap_min is not None:
        params["marketCapMoreThan"] = str(int(market_cap_min))
    if market_cap_max is not None:
        params["marketCapLowerThan"] = str(int(market_cap_max))
    if pe_min is not None:
        params["priceEarningsRatioMoreThan"] = str(pe_min)
    if pe_max is not None:
        params["priceEarningsRatioLowerThan"] = str(pe_max)
    if beta_min is not None:
        params["betaMoreThan"] = str(beta_min)
    if beta_max is not None:
        params["betaLowerThan"] = str(beta_max)
    if dividend_yield_min is not None:
        params["dividendMoreThan"] = str(dividend_yield_min)
    if sector:
        params["sector"] = sector
    if industry:
        params["industry"] = industry
    if exchange:
        params["exchange"] = exchange
    if is_etf is not None:
        params["isEtf"] = "true" if is_etf else "false"
    if is_actively_trading:
        params["isActivelyTrading"] = "true"

    cache_key = {**params}
    cached = cache.get("fmp_screen", cache_key, ttl_hours=6)
    if cached is not None:
        return cached

    params["apikey"] = _key()
    with _client() as c:
        r = c.get("/stock-screener", params=params)

    if r.status_code == 429:
        raise FMPRateLimitError("FMP daily quota exhausted (250/day on free tier).")
    if r.status_code == 401 or r.status_code == 403:
        raise FMPError(f"FMP auth error ({r.status_code}). Check FMP_API_KEY.")
    if r.status_code != 200:
        raise FMPError(f"FMP returned {r.status_code}: {r.text[:200]}")

    try:
        data = r.json()
    except Exception as e:
        raise FMPError(f"FMP response not JSON: {e}")

    if not isinstance(data, list):
        # FMP sometimes returns {"Error Message": "..."} for bad keys
        if isinstance(data, dict) and "Error Message" in data:
            raise FMPError(f"FMP: {data['Error Message']}")
        raise FMPError(f"FMP returned unexpected shape: {type(data).__name__}")

    cache.put("fmp_screen", cache_key, data)
    return data


def batch_quote(tickers: list[str]) -> list[dict[str, Any]]:
    """Latest quote for many tickers in one call. Up to 100 tickers per call.

    Returns list of dicts with: symbol, name, price, changesPercentage, change,
    dayLow, dayHigh, yearHigh, yearLow, marketCap, priceAvg50, priceAvg200,
    volume, avgVolume, exchange, open, previousClose, eps, pe, earningsAnnouncement,
    sharesOutstanding, timestamp.
    """
    if not tickers:
        return []
    # FMP accepts comma-separated tickers in path: /quote/AAPL,MSFT,GOOG
    # Cap to 100 per docs; chunk if needed.
    out: list[dict[str, Any]] = []
    for i in range(0, len(tickers), 100):
        chunk = tickers[i : i + 100]
        path = "/quote/" + ",".join(chunk)
        cache_key = {"tickers": ",".join(chunk)}
        cached = cache.get("fmp_quote", cache_key, ttl_hours=4)
        if cached is not None:
            out.extend(cached)
            continue

        with _client() as c:
            r = c.get(path, params={"apikey": _key()})
        if r.status_code == 429:
            raise FMPRateLimitError("FMP daily quota exhausted.")
        if r.status_code != 200:
            raise FMPError(f"FMP quote returned {r.status_code}: {r.text[:200]}")
        data = r.json()
        if isinstance(data, list):
            cache.put("fmp_quote", cache_key, data)
            out.extend(data)
    return out
