"""Real stock screener — filter a named universe by technical and fundamental criteria.

Distinct from `screen_universe` (in indicators.py), which screens a user-supplied
ticker list on technicals only. This tool takes a universe name (sp500, nasdaq100,
dow30) and supports fundamental filters via Tiingo's daily fundamentals endpoint.

Per-ticker work runs in a thread pool — each ticker is independent and bound by
HTTP latency, so threading turns a 5-minute serial scan into ~30 seconds.
"""
from concurrent.futures import ThreadPoolExecutor

from agents import function_tool

from vision import cache
from vision.data import tiingo
from vision.tools.indicators import _compute_indicators
from vision.tools.universes import get_universe


def _get_quick_fundamentals(ticker: str) -> dict:
    """Lightweight fundamentals fetch for screening — just the fields we filter on.

    Universe rows already carry sector/industry from the Wikipedia tables, so
    those don't need an API call. Tiingo's free tier limits fundamentals to
    Dow 30 — we surface that as `fundamentals_status` rather than blanking
    silently. Cache only when at least price OR fundamentals were retrieved."""
    params = {"ticker": ticker.upper()}
    cached = cache.get("_screen_fundamentals_tiingo", params, ttl_hours=24)
    if cached is not None:
        return cached

    metrics = tiingo.get_daily_metrics(ticker)
    summary = tiingo.get_price_summary(ticker, lookback_days=10)

    out = {
        "ticker": ticker.upper(),
        "name": None,
        "sector": None,
        "industry": None,
        "market_cap": metrics.get("market_cap"),
        "pe": metrics.get("pe_ratio"),
        "pb": metrics.get("pb_ratio"),
        "peg_1y": metrics.get("peg_ratio_1y"),
        "price": summary.last_close,
    }
    metric_error = metrics.get("error")
    if metric_error:
        out["fundamentals_status"] = metric_error  # tier_limited / rate_limited / no_data
        out["fundamentals_message"] = metrics.get("error_message")

    # Only cache when we got SOMETHING usable. If everything is blank,
    # don't poison the cache — try again next call.
    if out["price"] is not None or out["market_cap"] is not None:
        cache.put("_screen_fundamentals_tiingo", params, out)
    return out


def _screen_stocks(
    universe: str = "sp500",
    tickers: list[str] | None = None,
    sector: str | None = None,
    market_cap_min: float | None = None,
    market_cap_max: float | None = None,
    pe_min: float | None = None,
    pe_max: float | None = None,
    rsi_min: float | None = None,
    rsi_max: float | None = None,
    above_sma_50: bool | None = None,
    above_sma_200: bool | None = None,
    sort_by: str = "market_cap",
    limit: int = 50,
    skip_technicals: bool = False,
) -> dict:
    """Internal screener implementation. The agent-facing tool and the API
    route both call this directly to avoid round-tripping through the
    FunctionTool wrapper."""
    limit = min(limit, 200)

    if tickers:
        universe_rows = [{"ticker": t.upper(), "name": "", "sector": "", "industry": ""} for t in tickers]
        universe_label = f"custom ({len(tickers)} tickers)"
    else:
        universe_rows = get_universe(universe)
        universe_label = universe

    needs_technicals = (
        not skip_technicals
        and (rsi_min is not None or rsi_max is not None
             or above_sma_50 is not None or above_sma_200 is not None
             or sort_by == "rsi")
    )

    # First pass: cheap sector filter at the universe level — Wikipedia
    # already tells us the GICS sector, no API call needed.
    rows_to_fetch = []
    for row in universe_rows:
        if sector is not None:
            row_sector = row.get("sector") or ""
            if not row_sector or sector.lower() not in row_sector.lower():
                continue
        rows_to_fetch.append(row)

    # Second pass: parallel per-ticker fetch (fundamentals + indicators if needed)
    def _fetch_one(row: dict) -> tuple[dict, dict | None, dict | None, str | None]:
        """Returns (universe_row, fundamentals_or_None, indicators_or_None, error_or_None)."""
        t = row["ticker"]
        try:
            f = _get_quick_fundamentals(t)
        except Exception as e:
            return (row, None, None, str(e))
        ind = None
        if needs_technicals:
            try:
                ind = _compute_indicators(t, lookback_days=365)
            except Exception as e:
                return (row, f, None, f"indicators: {e}")
        return (row, f, ind, None)

    matches: list[dict] = []
    skipped: list[dict] = []
    notices: set[str] = set()

    # Thread-pool — each ticker is independent, bound by HTTP latency. With 16
    # workers the S&P 500 fetches in ~30s (vs ~5min serial).
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_fetch_one, rows_to_fetch))

    # Third pass: apply filters serially over the materialized results
    for row, f, ind, err in results:
        t = row["ticker"]
        if err and f is None:
            skipped.append({"ticker": t, "error": err})
            continue

        f = f or {}
        fstatus = f.get("fundamentals_status")
        if fstatus == "tier_limited":
            notices.add(
                "Tiingo's free tier limits fundamentals (P/E, P/B, market cap) to the Dow 30. "
                "Names outside the Dow 30 will show price + technicals only."
            )
        elif fstatus == "rate_limited":
            notices.add("Tiingo hourly request limit reached — fundamentals incomplete. Wait ~1h or upgrade.")

        mc = f.get("market_cap")
        if market_cap_min is not None and (mc is None or mc < market_cap_min):
            continue
        if market_cap_max is not None and (mc is None or mc > market_cap_max):
            continue
        pe = f.get("pe")
        if pe_min is not None and (pe is None or pe < pe_min):
            continue
        if pe_max is not None and (pe is None or pe > pe_max):
            continue

        record = dict(f)
        record["name"] = row.get("name") or record.get("name")
        record["sector"] = row.get("sector") or record.get("sector")
        record["industry"] = row.get("industry") or record.get("industry")

        if needs_technicals:
            if ind is None:
                continue
            if "error" in ind:
                continue
            rsi = ind.get("rsi_14")
            if rsi_min is not None and (rsi is None or rsi < rsi_min):
                continue
            if rsi_max is not None and (rsi is None or rsi > rsi_max):
                continue
            if above_sma_50 is True and not ind["trend"]["above_sma_50"]:
                continue
            if above_sma_200 is True and not ind["trend"]["above_sma_200"]:
                continue
            record["rsi_14"] = rsi
            record["above_sma_50"] = ind["trend"]["above_sma_50"]
            record["above_sma_200"] = ind["trend"]["above_sma_200"]

        matches.append(record)

    sort_key_map = {
        "market_cap": ("market_cap", True),
        "pe": ("pe", False),
        "rsi": ("rsi_14", False),
    }
    sort_key, descending = sort_key_map.get(sort_by, ("market_cap", True))
    matches.sort(
        key=lambda m: (m.get(sort_key) is None, -(m.get(sort_key) or 0) if descending else (m.get(sort_key) or 0))
    )

    return {
        "universe": universe_label,
        "n_screened": len(rows_to_fetch),
        "n_universe": len(universe_rows),
        "n_matches": len(matches),
        "n_returned": min(len(matches), limit),
        "filters": {
            "sector": sector,
            "market_cap_min": market_cap_min,
            "market_cap_max": market_cap_max,
            "pe_min": pe_min,
            "pe_max": pe_max,
            "rsi_min": rsi_min,
            "rsi_max": rsi_max,
            "above_sma_50": above_sma_50,
            "above_sma_200": above_sma_200,
        },
        "sort_by": sort_by,
        "matches": matches[:limit],
        "skipped_count": len(skipped),
        "notices": sorted(notices),
    }


@function_tool
def screen_stocks(
    universe: str = "sp500",
    tickers: list[str] | None = None,
    sector: str | None = None,
    market_cap_min: float | None = None,
    market_cap_max: float | None = None,
    pe_min: float | None = None,
    pe_max: float | None = None,
    rsi_min: float | None = None,
    rsi_max: float | None = None,
    above_sma_50: bool | None = None,
    above_sma_200: bool | None = None,
    sort_by: str = "market_cap",
    limit: int = 50,
    skip_technicals: bool = False,
) -> dict:
    """Screen a stock universe by fundamental and technical criteria.

    Universe can be a named index ("sp500", "nasdaq100", "dow30") OR a user-
    supplied ticker list via `tickers`. Filters are AND-combined; only names
    matching every supplied filter are returned. Pass only the filters you
    care about — every filter is optional.

    Args:
        universe: One of "sp500", "nasdaq100", "dow30". Ignored if `tickers` is provided.
        tickers: Optional explicit ticker list. Overrides `universe`.
        sector: GICS sector name to match (e.g., "Information Technology", "Energy").
        market_cap_min: Minimum market cap in USD (e.g., 10_000_000_000 for $10B+).
        market_cap_max: Maximum market cap in USD.
        pe_min: Minimum trailing P/E ratio.
        pe_max: Maximum trailing P/E ratio.
        rsi_min: Minimum RSI(14). Use 70 for overbought.
        rsi_max: Maximum RSI(14). Use 30 for oversold.
        above_sma_50: If True, only include names trading above their 50d SMA.
        above_sma_200: If True, only include names trading above their 200d SMA.
        sort_by: One of "market_cap", "pe", "rsi". Default "market_cap".
        limit: Max results. Default 50, max 200.
        skip_technicals: If True, skip RSI/SMA fetch (much faster). Use for fundamental-only screens.
    """
    return _screen_stocks(
        universe=universe,
        tickers=tickers,
        sector=sector,
        market_cap_min=market_cap_min,
        market_cap_max=market_cap_max,
        pe_min=pe_min,
        pe_max=pe_max,
        rsi_min=rsi_min,
        rsi_max=rsi_max,
        above_sma_50=above_sma_50,
        above_sma_200=above_sma_200,
        sort_by=sort_by,
        limit=limit,
        skip_technicals=skip_technicals,
    )
