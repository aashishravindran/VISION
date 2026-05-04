"""Tiingo client. Single source of truth for VISION's market data.

Free tier: 500 requests/day. We cache aggressively (12h prices, 3d market cap,
24h fundamentals) so even with multiple users the daily limit isn't an issue.

Endpoints used:
- /tiingo/daily/{ticker}              — meta (name, exchange, description)
- /tiingo/daily/{ticker}/prices       — EOD OHLCV
- /tiingo/fundamentals/{ticker}/daily — daily fundamentals (market cap, P/E, P/B)
- /tiingo/fundamentals/{ticker}/statements — full financial statements
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

import httpx

from vision import cache

TIINGO_BASE = "https://api.tiingo.com"


class TiingoError(RuntimeError):
    pass


def _key() -> str | None:
    return os.environ.get("TIINGO_API_KEY")


def _client() -> httpx.Client:
    key = _key()
    if not key:
        raise TiingoError("TIINGO_API_KEY not set in environment")
    return httpx.Client(
        base_url=TIINGO_BASE,
        headers={
            "Authorization": f"Token {key}",
            "Content-Type": "application/json",
        },
        timeout=15.0,
    )


class TiingoTierLimitError(TiingoError):
    """Tiingo returned 400/403 indicating the endpoint isn't available on this tier."""


class TiingoRateLimitError(TiingoError):
    """Tiingo returned 429 — hourly/daily quota exhausted."""


def _fetch_daily_fundamentals(ticker: str) -> list[dict] | None:
    """Pull and cache the full daily-fundamentals series for a ticker.

    Cached for 24h on success. On error we DO NOT cache, so transient failures
    (rate limit, network blip) don't poison the cache for hours.

    Raises TiingoTierLimitError or TiingoRateLimitError on 400/429 so callers
    can surface specific guidance instead of a blank None.
    """
    params = {"ticker": ticker.upper()}
    cached = cache.get("tiingo_daily_fund", params, ttl_hours=24)
    if cached is not None:
        return cached
    try:
        with _client() as c:
            r = c.get(f"/tiingo/fundamentals/{ticker.upper()}/daily")
    except TiingoError:
        raise
    except Exception:
        return None
    if r.status_code == 404:
        cache.put("tiingo_daily_fund", params, [])
        return []
    if r.status_code == 400 or r.status_code == 403:
        # Tiingo says: "Free and Power plans are limited to the DOW 30 ..."
        msg = (r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text)
        raise TiingoTierLimitError(f"Fundamentals unavailable for {ticker.upper()} on this Tiingo tier: {msg}")
    if r.status_code == 429:
        raise TiingoRateLimitError(
            f"Tiingo hourly/daily rate limit hit. Wait a bit or upgrade. (ticker={ticker.upper()})"
        )
    if r.status_code >= 500:
        return None  # transient — don't cache
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    cache.put("tiingo_daily_fund", params, data)
    return data


def get_market_cap(ticker: str) -> float | None:
    """Latest daily market cap. Returns None on tier limit / rate limit / no data
    rather than raising — for the heat map's bulk path where one missing ticker
    shouldn't break the whole call."""
    try:
        rows = _fetch_daily_fundamentals(ticker)
    except TiingoError:
        return None
    if not rows:
        return None
    latest = max(rows, key=lambda d: d.get("date") or "")
    return latest.get("marketCap")


def get_daily_metrics(ticker: str) -> dict[str, Any]:
    """Latest daily fundamental metrics: market cap, P/E, P/B, enterprise value,
    PEG ratio. Always returns a dict — with `error` populated if the endpoint
    isn't available for this ticker / tier / quota."""
    try:
        rows = _fetch_daily_fundamentals(ticker)
    except TiingoTierLimitError as e:
        return {
            "ticker": ticker.upper(),
            "error": "tier_limited",
            "error_message": str(e),
        }
    except TiingoRateLimitError as e:
        return {
            "ticker": ticker.upper(),
            "error": "rate_limited",
            "error_message": str(e),
        }
    except TiingoError as e:
        return {"ticker": ticker.upper(), "error": "tiingo_error", "error_message": str(e)}
    if rows is None:
        return {"ticker": ticker.upper(), "error": "fetch_failed"}
    if not rows:
        return {"ticker": ticker.upper(), "error": "no_data"}
    latest = max(rows, key=lambda d: d.get("date") or "")
    return {
        "ticker": ticker.upper(),
        "as_of": (latest.get("date") or "")[:10],
        "market_cap": latest.get("marketCap"),
        "enterprise_value": latest.get("enterpriseVal"),
        "pe_ratio": latest.get("peRatio"),
        "pb_ratio": latest.get("pbRatio"),
        "peg_ratio_1y": latest.get("trailingPEG1Y"),
    }


def get_market_caps_batch(tickers: Iterable[str], max_workers: int = 20) -> dict[str, float | None]:
    """Parallel batch market caps."""
    tickers = list(tickers)
    out: dict[str, float | None] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for t, mc in pool.map(lambda x: (x, get_market_cap(x)), tickers):
            out[t] = mc
    return out


@dataclass
class PriceSummary:
    ticker: str
    last_close: float | None
    ret_1d_pct: float | None
    ret_1w_pct: float | None
    ret_1m_pct: float | None
    ret_3m_pct: float | None
    ret_ytd_pct: float | None
    as_of: str | None


def _fetch_prices(ticker: str, lookback_days: int) -> list[dict] | None:
    end = datetime.utcnow().date()
    start = end - timedelta(days=lookback_days + 10)
    params = {"ticker": ticker.upper(), "lookback_days": lookback_days}
    cached = cache.get("tiingo_prices", params, ttl_hours=24)
    if cached is not None:
        return cached
    try:
        with _client() as c:
            r = c.get(
                f"/tiingo/daily/{ticker.upper()}/prices",
                params={"startDate": start.isoformat(), "endDate": end.isoformat()},
            )
    except TiingoError:
        return None
    except Exception:
        return None
    if r.status_code == 404:
        cache.put("tiingo_prices", params, [])
        return []
    if r.status_code == 429:
        # Rate limit — surface, don't poison cache
        raise TiingoRateLimitError(f"Tiingo rate limit on prices for {ticker.upper()}")
    if r.status_code != 200:
        return None  # don't cache transient errors
    try:
        data = r.json()
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    data.sort(key=lambda d: d.get("date", ""))
    if data:
        cache.put("tiingo_prices", params, data)
    return data


def get_price_summary(ticker: str, lookback_days: int = 60) -> PriceSummary:
    """Last close + 1d/1w/1m/3m/YTD returns from Tiingo daily EOD.

    Returns an empty PriceSummary on tier-limit / rate-limit / no-data — for
    callers (heat map, screener) that want graceful degradation across many
    tickers."""
    try:
        rows = _fetch_prices(ticker, lookback_days) or []
    except TiingoError:
        return PriceSummary(ticker.upper(), None, None, None, None, None, None, None)
    if not rows:
        return PriceSummary(ticker.upper(), None, None, None, None, None, None, None)

    closes = [float(r.get("adjClose") or r.get("close") or 0) for r in rows]
    closes = [c for c in closes if c > 0]
    if len(closes) < 2:
        return PriceSummary(ticker.upper(), None, None, None, None, None, None, None)

    def pct(days: int) -> float | None:
        if len(closes) <= days:
            return None
        return float((closes[-1] / closes[-1 - days] - 1) * 100)

    last_date = (rows[-1].get("date") or "")[:10]
    ytd_year = last_date[:4] if last_date else str(datetime.utcnow().year)
    ytd_rows = [r for r in rows if (r.get("date") or "")[:4] == ytd_year]
    ytd_open = (
        float(ytd_rows[0].get("adjClose") or ytd_rows[0].get("close") or 0)
        if ytd_rows else 0
    )
    ret_ytd = (
        round(float((closes[-1] / ytd_open - 1) * 100), 2) if ytd_open > 0 else None
    )

    return PriceSummary(
        ticker=ticker.upper(),
        last_close=round(closes[-1], 2),
        ret_1d_pct=round(pct(1), 2) if pct(1) is not None else None,
        ret_1w_pct=round(pct(5), 2) if pct(5) is not None else None,
        ret_1m_pct=round(pct(21), 2) if pct(21) is not None else None,
        ret_3m_pct=round(pct(63), 2) if pct(63) is not None else None,
        ret_ytd_pct=ret_ytd,
        as_of=last_date or None,
    )


def get_price_summaries_batch(
    tickers: Iterable[str], lookback_days: int = 60, max_workers: int = 16
) -> dict[str, PriceSummary]:
    """Parallel batch price summaries. Each is a separate Tiingo request — be
    mindful of the 500/day free-tier limit (we cache 12h)."""
    tickers = list(tickers)
    out: dict[str, PriceSummary] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for s in pool.map(lambda t: get_price_summary(t, lookback_days), tickers):
            out[s.ticker] = s
    return out


def get_meta(ticker: str) -> dict[str, Any] | None:
    """Basic ticker metadata (name, exchange, description)."""
    params = {"ticker": ticker.upper()}
    cached = cache.get("tiingo_meta", params, ttl_hours=24 * 30)
    if cached is not None:
        return cached
    try:
        with _client() as c:
            r = c.get(f"/tiingo/daily/{ticker.upper()}")
            if r.status_code == 404:
                cache.put("tiingo_meta", params, {})
                return {}
            r.raise_for_status()
            data = r.json()
    except Exception:
        return None
    cache.put("tiingo_meta", params, data)
    return data


def get_price_history(ticker: str, lookback_days: int = 365) -> list[dict]:
    """Full price history as a list of {date, open, high, low, close, volume,
    adjClose, ...} rows. Wraps `_fetch_prices` for tools that need raw data.

    Returns [] on rate-limit / network failures — callers (chart endpoint,
    indicators, screener) should detect the empty list and surface a helpful
    error to the user rather than crashing."""
    try:
        return _fetch_prices(ticker, lookback_days) or []
    except TiingoError:
        return []


def get_statements(ticker: str) -> dict[str, Any] | None:
    """Full financial statements (income, balance sheet, cash flow) per period.

    Tiingo returns a list of statement reports keyed by year/quarter. We
    surface the most recent four annual reports by default. Cached 24h."""
    params = {"ticker": ticker.upper()}
    cached = cache.get("tiingo_statements", params, ttl_hours=24)
    if cached is not None:
        return cached
    try:
        with _client() as c:
            r = c.get(
                f"/tiingo/fundamentals/{ticker.upper()}/statements",
                params={"asReported": "false"},
            )
            if r.status_code == 404:
                cache.put("tiingo_statements", params, {})
                return {}
            r.raise_for_status()
            data = r.json()
    except TiingoError:
        return None
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    # Tiingo returns one record per period. Each has statementData with
    # 'incomeStatement', 'balanceSheet', 'cashFlow' lists of {dataCode, value}
    annual = [d for d in data if d.get("quarter") == 0]
    annual.sort(key=lambda d: d.get("year") or 0, reverse=True)
    out = {
        "ticker": ticker.upper(),
        "annual_periods": annual[:4],  # last 4 annual reports
        "n_periods_available": len(annual),
    }
    cache.put("tiingo_statements", params, out)
    return out
