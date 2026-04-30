"""Sector ETF performance backed by Tiingo.

`get_sector_holdings` (top constituents of each SPDR ETF) was previously a
yfinance-only feature. We've dropped that endpoint — for sector heavyweights,
use the screener with `sector=...` to find the largest names by market cap.
"""
from agents import function_tool

from vision import cache
from vision.config import BENCHMARK_ETFS, SECTOR_ETFS
from vision.data import tiingo


@function_tool
def get_sector_performance(lookback_days: int = 90) -> dict:
    """Get returns for the 11 SPDR sector ETFs and key benchmarks.

    Returns 1-day, 1-week, 1-month, 3-month, and YTD returns for each sector
    plus SPY/QQQ/IWM/DIA benchmarks. Use this to identify sector rotation,
    leadership shifts, and overall market regime.

    Args:
        lookback_days: How much history to fetch. Default 90 (covers 3-month return).
    """
    params = {"lookback_days": lookback_days}
    cached = cache.get("get_sector_performance", params, ttl_hours=4)
    if cached is not None:
        return cached

    tickers = list(SECTOR_ETFS.keys()) + list(BENCHMARK_ETFS.keys())
    summaries = tiingo.get_price_summaries_batch(tickers, lookback_days=max(lookback_days, 90))

    rows = []
    for t in tickers:
        s = summaries.get(t.upper())
        if s is None or s.last_close is None:
            continue
        rows.append({
            "ticker": t,
            "name": SECTOR_ETFS.get(t) or BENCHMARK_ETFS.get(t),
            "kind": "sector" if t in SECTOR_ETFS else "benchmark",
            "last_close": s.last_close,
            "return_1d_pct": s.ret_1d_pct,
            "return_1w_pct": s.ret_1w_pct,
            "return_1m_pct": s.ret_1m_pct,
            "return_3m_pct": s.ret_3m_pct,
            "return_ytd_pct": s.ret_ytd_pct,
        })

    rows.sort(key=lambda r: (r["kind"] != "sector", -(r["return_1m_pct"] or 0)))
    out = {
        "source": "Tiingo (EOD)",
        "sectors": [r for r in rows if r["kind"] == "sector"],
        "benchmarks": [r for r in rows if r["kind"] == "benchmark"],
    }
    cache.put("get_sector_performance", params, out)
    return out
