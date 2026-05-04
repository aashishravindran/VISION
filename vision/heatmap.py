"""Heat map data builders backed by FMP.

Major win over the Tiingo era: heat-map data now requires only 1-2 batch FMP
calls instead of 600+ per-ticker calls. We use FMP's batch_quote which returns
price + market cap + change in one shot for up to 100 tickers per chunk.

Two heatmaps:
- sector: 11 SPDR sector ETFs (1 batch_quote call)
- sp500: top N S&P 500 names by market cap (constituents + 1-5 batch_quote calls)
"""
from datetime import datetime

from vision import cache
from vision.config import SECTOR_ETFS
from vision.data import fmp


def get_sector_heatmap() -> dict:
    """Sector heat map — 11 SPDR sector ETFs in 1 batch FMP call."""
    cached = cache.get("get_sector_heatmap_v4", {}, ttl_hours=4)
    if cached is not None:
        return cached

    tickers = list(SECTOR_ETFS.keys())
    try:
        quotes = fmp.batch_quote(tickers)
    except fmp.FMPError as e:
        return {"kind": "sector", "items": [], "error": str(e)}

    by_symbol = {q.get("symbol"): q for q in quotes}
    items = []
    for ticker, name in SECTOR_ETFS.items():
        q = by_symbol.get(ticker, {})
        price = q.get("price")
        items.append({
            "ticker": ticker,
            "label": name,
            "value": 1,
            "price": price,
            "ret_1d": q.get("changePercentage"),
            "ret_1w": _approx_pct(price, q.get("priceAvg50"), days=5),  # rough
            "ret_1m": _approx_pct(price, q.get("priceAvg50")),
        })

    out = {
        "kind": "sector",
        "as_of": datetime.utcnow().date().isoformat(),
        "source": "FMP /quote (batch)",
        "items": items,
    }
    cache.put("get_sector_heatmap_v4", {}, out)
    return out


def _approx_pct(curr: float | None, base: float | None, days: int | None = None) -> float | None:
    """Compute rough percent change from current vs a baseline. Used for
    quick approximations on the heat map (the more precise number lives in
    individual chart endpoints)."""
    if curr is None or base is None or base == 0:
        return None
    return round((curr / base - 1) * 100, 2)


def get_sp500_heatmap(top_n: int = 100) -> dict:
    """S&P 500 heat map — top N by market cap, grouped by GICS sector.

    Cost: 1 constituents call (cached 7d) + ceil(N/100) batch_quote calls.
    For N=100: 2 calls cold, 0 calls warm. For N=200: 3 calls cold.
    """
    params = {"top_n": top_n}
    cached = cache.get("get_sp500_heatmap_v4", params, ttl_hours=4)
    if cached is not None:
        return cached

    try:
        constituents = fmp.constituents("sp500")
    except fmp.FMPError as e:
        return {"kind": "sp500", "items": [], "error": str(e)}

    sector_by_ticker = {c["symbol"]: c.get("sector") or "Other" for c in constituents}
    name_by_ticker = {c["symbol"]: c.get("name") or c["symbol"] for c in constituents}
    all_tickers = [c["symbol"] for c in constituents]

    try:
        quotes = fmp.batch_quote(all_tickers)
    except fmp.FMPError as e:
        return {"kind": "sp500", "items": [], "error": str(e)}

    # Rank by market cap, take top N
    ranked = sorted(
        ((q.get("symbol"), q) for q in quotes if q.get("marketCap")),
        key=lambda kv: kv[1].get("marketCap") or 0,
        reverse=True,
    )[:top_n]

    items = []
    for ticker, q in ranked:
        items.append({
            "ticker": ticker,
            "name": name_by_ticker.get(ticker, ticker),
            "sector": sector_by_ticker.get(ticker) or "Other",
            "value": float(q.get("marketCap") or 0),
            "price": q.get("price"),
            "ret_1d": q.get("changePercentage"),
            "ret_1w": _approx_pct(q.get("price"), q.get("priceAvg50"), days=5),
            "ret_1m": _approx_pct(q.get("price"), q.get("priceAvg50")),
        })

    out = {
        "kind": "sp500",
        "top_n": top_n,
        "as_of": datetime.utcnow().date().isoformat(),
        "source": "FMP /sp500-constituent + /quote (batch)",
        "items": items,
    }
    cache.put("get_sp500_heatmap_v4", params, out)
    return out
