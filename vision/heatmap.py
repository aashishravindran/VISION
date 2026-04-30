"""Heat map data builders. Returns JSON the frontend renders as a Plotly treemap.

Uses Tiingo for both prices and market caps (Stooq's free CSV API was closed off).
yfinance is no longer on this path — Yahoo's crumb invalidation made it unreliable
for bulk operations.

Free tier note: Tiingo allows 500 requests/day. The sector heat map needs 11
requests; the S&P 500 heat map needs ~500-600 (top-N selection + prices). Heat
maps are cached for 4h, so a 2-3x daily refresh stays well within limits.
"""
from datetime import datetime

from vision import cache
from vision.config import SECTOR_ETFS
from vision.data import tiingo
from vision.tools.universes import get_universe


def get_sector_heatmap() -> dict:
    """Sector heat map: 11 SPDR sector ETFs from Tiingo."""
    cached = cache.get("get_sector_heatmap_v3", {}, ttl_hours=4)
    if cached is not None:
        return cached

    summaries = tiingo.get_price_summaries_batch(SECTOR_ETFS.keys(), lookback_days=60)

    items = []
    for ticker, name in SECTOR_ETFS.items():
        s = summaries.get(ticker.upper())
        if s is None:
            continue
        items.append({
            "ticker": ticker,
            "label": name,
            "value": 1,  # equal-weighted
            "price": s.last_close,
            "ret_1d": s.ret_1d_pct,
            "ret_1w": s.ret_1w_pct,
            "ret_1m": s.ret_1m_pct,
            "as_of": s.as_of,
        })

    out = {
        "kind": "sector",
        "as_of": datetime.utcnow().date().isoformat(),
        "source": "Tiingo (EOD)",
        "items": items,
    }
    cache.put("get_sector_heatmap_v3", {}, out)
    return out


def get_sp500_heatmap(top_n: int = 100) -> dict:
    """S&P 500 heat map: top N names by market cap, grouped by GICS sector.

    Both market caps and prices come from Tiingo. First run with cold cache:
    ~30s for market caps + ~30s for prices. Re-runs (within 4h cache TTL): < 1s.
    """
    params = {"top_n": top_n}
    cached = cache.get("get_sp500_heatmap_v3", params, ttl_hours=4)
    if cached is not None:
        return cached

    universe = get_universe("sp500")
    sector_by_ticker = {u["ticker"]: u.get("sector") or "" for u in universe}
    name_by_ticker = {u["ticker"]: u.get("name") or u["ticker"] for u in universe}

    tickers = [u["ticker"] for u in universe]

    # Step 1: market caps via Tiingo (parallel, cached 3 days)
    market_caps = tiingo.get_market_caps_batch(tickers)

    # Step 2: pick top N by market cap
    ranked = sorted(
        ((t, mc) for t, mc in market_caps.items() if mc),
        key=lambda kv: kv[1],
        reverse=True,
    )[:top_n]
    top_tickers = [t for t, _ in ranked]

    if not top_tickers:
        return {
            "kind": "sp500",
            "top_n": top_n,
            "as_of": datetime.utcnow().date().isoformat(),
            "items": [],
            "error": "No market caps available — check TIINGO_API_KEY",
        }

    # Step 3: returns via Tiingo (parallel, cached 12h)
    summaries = tiingo.get_price_summaries_batch(top_tickers, lookback_days=60)

    items = []
    for t in top_tickers:
        s = summaries.get(t)
        if s is None:
            continue
        items.append({
            "ticker": t,
            "name": name_by_ticker.get(t, t),
            "sector": sector_by_ticker.get(t) or "Other",
            "value": float(market_caps.get(t) or 0),
            "price": s.last_close,
            "ret_1d": s.ret_1d_pct,
            "ret_1w": s.ret_1w_pct,
            "ret_1m": s.ret_1m_pct,
        })

    out = {
        "kind": "sp500",
        "top_n": top_n,
        "as_of": datetime.utcnow().date().isoformat(),
        "source": "Tiingo (prices + market caps)",
        "items": items,
    }
    cache.put("get_sp500_heatmap_v3", params, out)
    return out
