"""Financial Modeling Prep client — VISION's single data source.

FMP `/stable/` covers everything we need: per-ticker quote/fundamentals,
historical OHLCV, financial statements, server-side stock screener (with
industry-level filtering), sector performance, forward earnings calendar,
index constituents, and macro (treasury rates).

Free tier: 250 requests/day. We cache aggressively (4-24h depending on
endpoint) so a normal day's usage sits at ~50-100 calls.

All functions return clean dicts/lists. Errors raise FMPError /
FMPRateLimitError / FMPNoKeyError so callers can surface specific guidance.

Note: FMP migrated from /api/v3/ to /stable/ on Aug 31, 2025. Keys issued
after that date only work against /stable/.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import httpx

from vision import cache

FMP_BASE = "https://financialmodelingprep.com/stable"


class FMPError(RuntimeError):
    pass


class FMPNoKeyError(FMPError):
    """FMP_API_KEY not set — caller should fall back or ask the user to configure."""


class FMPRateLimitError(FMPError):
    """FMP returned 429 — daily quota hit."""


class FMPTierGatedError(FMPError):
    """FMP returned 402 / 'Premium Query Parameter' — this specific ticker
    or filter value isn't covered by the free tier. Caller should fall back
    to the web-lookup tool for ticker queries, or note the gap to the user."""


def _key() -> str:
    k = os.environ.get("FMP_API_KEY")
    if not k:
        raise FMPNoKeyError("FMP_API_KEY not set in environment")
    return k


def _client() -> httpx.Client:
    return httpx.Client(base_url=FMP_BASE, timeout=20.0)


def _get(path: str, *, params: dict[str, Any] | None = None, cache_key: dict | None = None,
         ttl_hours: float = 6) -> Any:
    """Shared GET helper with caching + clean error mapping."""
    if cache_key is not None:
        cached = cache.get(f"fmp{path}", cache_key, ttl_hours=ttl_hours)
        if cached is not None:
            return cached

    full_params = {"apikey": _key(), **(params or {})}
    with _client() as c:
        r = c.get(path, params=full_params)

    if r.status_code == 429:
        raise FMPRateLimitError(
            "FMP daily quota exhausted (250/day on free tier). Resets at UTC midnight."
        )
    if r.status_code == 402:
        # Specific "this ticker / filter value needs a paid plan" — distinct
        # from quota or auth issues so the agent can fall back appropriately.
        raise FMPTierGatedError(
            f"FMP free tier doesn't cover this query: {r.text[:200]}"
        )
    if r.status_code in (401, 403):
        raise FMPError(f"FMP auth error ({r.status_code}). Check FMP_API_KEY validity.")
    if r.status_code != 200:
        # Some endpoints embed the "Premium Query Parameter" text in a 200 body
        # depending on FMP's mood — handle both shapes.
        raise FMPError(f"FMP {path} returned {r.status_code}: {r.text[:200]}")

    try:
        data = r.json()
    except Exception as e:
        raise FMPError(f"FMP {path} response not JSON: {e}")
    # Tier-gated errors sometimes come back as a 200 with an "Error Message"
    # body. Surface those as FMPTierGatedError too when the message smells like it.
    if isinstance(data, dict) and "Error Message" in data:
        msg = data["Error Message"]
        if "Premium" in msg or "subscription" in msg.lower() or "Special Endpoint" in msg:
            raise FMPTierGatedError(f"FMP: {msg}")
        raise FMPError(f"FMP: {msg}")

    if cache_key is not None:
        cache.put(f"fmp{path}", cache_key, data)
    return data


# ============================================================================
# Screener — server-side filter (the cornerstone)
# ============================================================================

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
    country: str | None = None,
    exchange: str | None = "nasdaq,nyse",
    is_etf: bool | None = False,
    is_actively_trading: bool = True,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Server-side stock screener via /company-screener.

    Industry filter unlocks thematic queries — e.g. industry='Gold' or
    industry='Other Precious Metals' returns mining companies. Use
    list_industries() to see the full ~150 valid industry names.

    Cached 6h on identical filter sets.
    """
    params: dict[str, str] = {"limit": str(min(limit, 1000))}
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
    if country:
        params["country"] = country
    if exchange:
        params["exchange"] = exchange
    if is_etf is not None:
        params["isEtf"] = "true" if is_etf else "false"
    if is_actively_trading:
        params["isActivelyTrading"] = "true"

    data = _get("/company-screener", params=params, cache_key=params, ttl_hours=6)
    if not isinstance(data, list):
        raise FMPError(f"unexpected screener shape: {type(data).__name__}")
    return data


# ============================================================================
# Quotes — single ticker and batch
# ============================================================================

def batch_quote(tickers: list[str]) -> list[dict[str, Any]]:
    """Latest quote for many tickers (up to 100 per chunk).

    Returns: symbol, name, price, changePercentage, change, dayLow, dayHigh,
    yearHigh, yearLow, marketCap, priceAvg50, priceAvg200, volume, avgVolume,
    exchange, open, previousClose, eps, pe, earningsAnnouncement, sharesOutstanding.
    """
    if not tickers:
        return []
    out: list[dict[str, Any]] = []
    for i in range(0, len(tickers), 100):
        chunk = tickers[i : i + 100]
        symbols = ",".join(chunk)
        data = _get("/quote", params={"symbol": symbols},
                    cache_key={"symbols": symbols}, ttl_hours=4)
        if isinstance(data, list):
            out.extend(data)
    return out


def single_quote(ticker: str) -> dict[str, Any] | None:
    """Latest quote for one ticker (FMP wraps in a list of 1)."""
    rows = batch_quote([ticker])
    return rows[0] if rows else None


# ============================================================================
# Historical OHLCV
# ============================================================================

def historical_prices(ticker: str, days: int = 365) -> list[dict[str, Any]]:
    """Daily OHLCV for `days` calendar days back. Cached 24h.

    Each row: {symbol, date, open, high, low, close, volume, change, changePercent, vwap}.
    Newest first by default; we sort oldest→newest before returning."""
    end = date.today()
    start = end - timedelta(days=days + 10)
    cache_key = {"ticker": ticker.upper(), "from": start.isoformat(), "to": end.isoformat()}
    cached = cache.get("fmp/historical-price-eod", cache_key, ttl_hours=24)
    if cached is not None:
        return cached

    data = _get("/historical-price-eod/full",
                params={"symbol": ticker.upper(), "from": start.isoformat(), "to": end.isoformat()})
    if not isinstance(data, list):
        return []
    data.sort(key=lambda r: r.get("date") or "")
    cache.put("fmp/historical-price-eod", cache_key, data)
    return data


# ============================================================================
# Fundamentals — statements + key metrics + profile
# ============================================================================

def income_statement(ticker: str, limit: int = 4, period: str = "annual") -> list[dict[str, Any]]:
    """Annual or quarterly income statement. Cached 24h."""
    return _get("/income-statement",
                params={"symbol": ticker.upper(), "limit": str(limit), "period": period},
                cache_key={"ticker": ticker.upper(), "limit": limit, "period": period},
                ttl_hours=24) or []


def balance_sheet(ticker: str, limit: int = 4, period: str = "annual") -> list[dict[str, Any]]:
    """Annual or quarterly balance sheet. Cached 24h."""
    return _get("/balance-sheet-statement",
                params={"symbol": ticker.upper(), "limit": str(limit), "period": period},
                cache_key={"ticker": ticker.upper(), "limit": limit, "period": period},
                ttl_hours=24) or []


def cash_flow(ticker: str, limit: int = 4, period: str = "annual") -> list[dict[str, Any]]:
    """Annual or quarterly cash flow statement. Cached 24h."""
    return _get("/cash-flow-statement",
                params={"symbol": ticker.upper(), "limit": str(limit), "period": period},
                cache_key={"ticker": ticker.upper(), "limit": limit, "period": period},
                ttl_hours=24) or []


def key_metrics(ticker: str, limit: int = 4) -> list[dict[str, Any]]:
    """Key financial metrics: marketCap, enterpriseValue, peRatio, pbRatio,
    roe, roic, currentRatio, debtToEquity, freeCashFlowYield, etc. Cached 24h."""
    return _get("/key-metrics",
                params={"symbol": ticker.upper(), "limit": str(limit)},
                cache_key={"ticker": ticker.upper(), "limit": limit},
                ttl_hours=24) or []


def ratios(ticker: str, limit: int = 4) -> list[dict[str, Any]]:
    """Financial ratios: grossProfitMargin, operatingMargin, netProfitMargin,
    currentRatio, quickRatio, debtRatio, P/E, P/B, etc. Cached 24h."""
    return _get("/ratios",
                params={"symbol": ticker.upper(), "limit": str(limit)},
                cache_key={"ticker": ticker.upper(), "limit": limit},
                ttl_hours=24) or []


def profile(ticker: str) -> dict[str, Any] | None:
    """Company profile: name, sector, industry, description, exchange,
    employees, CEO, IPO date, market cap. Cached 7 days (rarely changes)."""
    data = _get("/profile",
                params={"symbol": ticker.upper()},
                cache_key={"ticker": ticker.upper()},
                ttl_hours=24 * 7)
    if isinstance(data, list) and data:
        return data[0]
    return None


# ============================================================================
# Sector performance + earnings calendar
# ============================================================================

def historical_sector_performance(sector: str | None = None, days: int = 30) -> list[dict[str, Any]]:
    """Daily sector returns. Pass `sector` to filter (e.g. "Technology"),
    omit for all sectors. Returns rows of {date, sector, exchange, averageChange}.
    Cached 4h."""
    end = date.today()
    start = end - timedelta(days=days + 5)
    params: dict[str, str] = {"from": start.isoformat(), "to": end.isoformat()}
    if sector:
        params["sector"] = sector
    return _get("/historical-sector-performance",
                params=params,
                cache_key={"sector": sector or "all", "days": days},
                ttl_hours=4) or []


def earnings_calendar(from_date: str | None = None, to_date: str | None = None,
                      ticker: str | None = None) -> list[dict[str, Any]]:
    """Earnings calendar (past + future). Each row: {symbol, date, epsActual,
    epsEstimated, revenueActual, revenueEstimated, lastUpdated}.

    Pass `from_date`/`to_date` (YYYY-MM-DD) to scope. Without dates, FMP
    returns recent ±1 week. Cached 6h."""
    if from_date is None:
        from_date = (date.today() - timedelta(days=7)).isoformat()
    if to_date is None:
        to_date = (date.today() + timedelta(days=30)).isoformat()
    params = {"from": from_date, "to": to_date}
    if ticker:
        params["symbol"] = ticker.upper()
    return _get("/earnings-calendar",
                params=params,
                cache_key={"from": from_date, "to": to_date, "ticker": ticker or ""},
                ttl_hours=6) or []


# ============================================================================
# Index constituents (replaces Wikipedia scrape)
# ============================================================================

_CONSTITUENT_PATHS = {
    "sp500": "/sp500-constituent",
    "nasdaq100": "/nasdaq-constituent",
    "dow30": "/dowjones-constituent",
}


def constituents(name: str) -> list[dict[str, Any]]:
    """Index members. Each row: {symbol, name, sector, subSector, headQuarter,
    dateFirstAdded, cik, founded}. Cached 7 days. Mirrors the shape we expected
    from the Wikipedia scrape (with `subSector` instead of `industry`).
    """
    path = _CONSTITUENT_PATHS.get(name.lower())
    if not path:
        raise ValueError(f"unknown universe: {name}. Choose from {list(_CONSTITUENT_PATHS)}")
    rows = _get(path, cache_key={"name": name}, ttl_hours=24 * 7) or []
    # Normalize symbols (FMP uses dot, Tiingo/yfinance use dash for class shares)
    for r in rows:
        if r.get("symbol"):
            r["symbol"] = r["symbol"].replace(".", "-").upper()
    return rows


# ============================================================================
# Industry / sector taxonomy
# ============================================================================

def list_industries() -> list[str]:
    """All valid `industry` filter values for screen()."""
    rows = _get("/available-industries", cache_key={}, ttl_hours=24 * 30) or []
    return [r["industry"] for r in rows if isinstance(r, dict) and r.get("industry")]


def list_sectors() -> list[str]:
    """All valid `sector` filter values for screen()."""
    rows = _get("/available-sectors", cache_key={}, ttl_hours=24 * 30) or []
    return [r["sector"] for r in rows if isinstance(r, dict) and r.get("sector")]


# ============================================================================
# Macro (for v0.7+ macro dashboard, but keep wired now)
# ============================================================================

def treasury_rates(from_date: str | None = None, to_date: str | None = None) -> list[dict[str, Any]]:
    """US Treasury yield curve daily series. Cached 12h."""
    if from_date is None:
        from_date = (date.today() - timedelta(days=30)).isoformat()
    if to_date is None:
        to_date = date.today().isoformat()
    return _get("/treasury-rates",
                params={"from": from_date, "to": to_date},
                cache_key={"from": from_date, "to": to_date},
                ttl_hours=12) or []
