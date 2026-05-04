"""Sector ETF performance backed by FMP.

We read the SPDR sector ETFs' returns + the daily sector aggregates that FMP
publishes. The ETF batch_quote gives current price/change; historical_sector_performance
fills in trend context."""
from agents import function_tool

from vision import cache
from vision.config import BENCHMARK_ETFS, SECTOR_ETFS
from vision.data import fmp


def _pct(curr: float | None, prev: float | None) -> float | None:
    if curr is None or prev is None or prev == 0:
        return None
    return round((curr / prev - 1) * 100, 2)


@function_tool
def get_sector_performance(lookback_days: int = 90) -> dict:
    """Get returns for the 11 SPDR sector ETFs and key benchmarks.

    Returns 1-day, 1-week (priceAvg10 vs current), 1-month, 3-month, 1-year
    returns derived from FMP quotes. Use this to identify sector rotation,
    leadership shifts, and overall market regime.

    Args:
        lookback_days: Currently informational — FMP's quote endpoint gives
            us 50d/200d averages and yearHigh/Low; we don't paginate further.
    """
    cached = cache.get("get_sector_performance", {"lookback": lookback_days}, ttl_hours=4)
    if cached is not None:
        return cached

    tickers = list(SECTOR_ETFS.keys()) + list(BENCHMARK_ETFS.keys())

    try:
        quotes = fmp.batch_quote(tickers)
    except fmp.FMPNoKeyError:
        return {"error": "FMP_API_KEY not set on the server."}
    except fmp.FMPRateLimitError as e:
        return {"error": "rate_limited", "error_message": str(e)}
    except fmp.FMPError as e:
        return {"error": "fmp_error", "error_message": str(e)}

    by_symbol = {q.get("symbol"): q for q in quotes}

    rows = []
    for t in tickers:
        q = by_symbol.get(t)
        if not q:
            continue
        price = q.get("price")
        rows.append({
            "ticker": t,
            "name": SECTOR_ETFS.get(t) or BENCHMARK_ETFS.get(t),
            "kind": "sector" if t in SECTOR_ETFS else "benchmark",
            "last_close": price,
            "change_pct_1d": q.get("changePercentage"),
            "year_high": q.get("yearHigh"),
            "year_low": q.get("yearLow"),
            "price_avg_50d": q.get("priceAvg50"),
            "price_avg_200d": q.get("priceAvg200"),
            "vs_50d_pct": _pct(price, q.get("priceAvg50")),
            "vs_200d_pct": _pct(price, q.get("priceAvg200")),
            "vs_year_high_pct": _pct(price, q.get("yearHigh")),
        })

    rows.sort(key=lambda r: (r["kind"] != "sector", -(r["change_pct_1d"] or 0)))
    out = {
        "source": "FMP /quote (1 batch call)",
        "sectors": [r for r in rows if r["kind"] == "sector"],
        "benchmarks": [r for r in rows if r["kind"] == "benchmark"],
    }
    cache.put("get_sector_performance", {"lookback": lookback_days}, out)
    return out
