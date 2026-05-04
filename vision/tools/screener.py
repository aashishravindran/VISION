"""Real stock screener — filter a stock universe by technical and fundamental criteria.

Two strategies:
1. **FMP fast path** — when FMP_API_KEY is set, hand the fundamental filters
   (market cap, P/E, sector) to FMP's `/stock-screener` endpoint and let it
   filter server-side in ONE request. Massive cost reduction vs per-ticker.
2. **Tiingo fallback path** — per-ticker fetches from Tiingo, parallelized
   via a thread pool. Hits Tiingo's tier-limit on non-Dow names, but works
   with whatever data we can get.

Technical filters (RSI, SMA trend) are always computed locally from Tiingo
prices — neither FMP free nor Tiingo offer pre-computed RSI in batch.
"""
from concurrent.futures import ThreadPoolExecutor

from agents import function_tool

from vision import cache
from vision.data import fmp, tiingo
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


def _try_fmp_screen(
    *,
    sector: str | None,
    market_cap_min: float | None,
    market_cap_max: float | None,
    pe_min: float | None,
    pe_max: float | None,
    universe_filter: list[str] | None,
    limit: int,
) -> tuple[list[dict] | None, str | None]:
    """Attempt FMP server-side screen. Returns (rows, source_label) on success,
    (None, error_reason) on any failure so caller can fall back to Tiingo."""
    try:
        rows = fmp.screen(
            market_cap_min=market_cap_min,
            market_cap_max=market_cap_max,
            pe_min=pe_min,
            pe_max=pe_max,
            sector=sector,
            limit=max(limit * 2, 100),  # over-fetch for technicals filter
        )
    except fmp.FMPNoKeyError:
        return None, "no_key"
    except fmp.FMPRateLimitError:
        return None, "rate_limited"
    except fmp.FMPError as e:
        return None, f"error: {e}"

    # Translate FMP's response shape to our internal record shape
    out: list[dict] = []
    universe_filter_set = (
        {t.upper() for t in universe_filter} if universe_filter else None
    )
    for r in rows:
        sym = (r.get("symbol") or "").upper()
        if not sym:
            continue
        if universe_filter_set and sym not in universe_filter_set:
            continue
        out.append({
            "ticker": sym,
            "name": r.get("companyName"),
            "sector": r.get("sector"),
            "industry": r.get("industry"),
            "market_cap": r.get("marketCap"),
            "pe": None,  # FMP screener doesn't return P/E directly; we'd need a quote join
            "pb": None,
            "peg_1y": None,
            "price": r.get("price"),
            "beta": r.get("beta"),
            "volume": r.get("volume"),
            "exchange": r.get("exchangeShortName") or r.get("exchange"),
            "_source": "fmp",
        })
    return out, "fmp"


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
    notices: set[str] = set()

    # === FMP fast path ===
    # If FMP is configured, skip the per-ticker Tiingo loop entirely. FMP
    # handles sector/marketcap/PE filtering server-side; we still need
    # Tiingo for technicals if requested.
    universe_filter = (
        [r["ticker"] for r in universe_rows]
        if not tickers and universe in ("sp500", "nasdaq100", "dow30")
        else None
    )
    fmp_rows, fmp_status = _try_fmp_screen(
        sector=sector,
        market_cap_min=market_cap_min,
        market_cap_max=market_cap_max,
        pe_min=pe_min,
        pe_max=pe_max,
        universe_filter=universe_filter,
        limit=limit,
    )

    if fmp_rows is not None:
        # FMP handled fundamentals. Optionally enrich with technicals from Tiingo.
        if not needs_technicals:
            fmp_rows.sort(
                key=lambda m: (m.get("market_cap") is None, -(m.get("market_cap") or 0))
                if sort_by == "market_cap"
                else (m.get(sort_by) is None, -(m.get(sort_by) or 0))
            )
            return {
                "universe": universe_label,
                "n_screened": len(fmp_rows),
                "n_universe": len(universe_rows) if not tickers else len(tickers),
                "n_matches": len(fmp_rows),
                "n_returned": min(len(fmp_rows), limit),
                "filters": _filter_dict(sector, market_cap_min, market_cap_max, pe_min, pe_max,
                                         rsi_min, rsi_max, above_sma_50, above_sma_200),
                "sort_by": sort_by,
                "matches": fmp_rows[:limit],
                "skipped_count": 0,
                "notices": [],
                "sources_used": ["fmp"],
            }

        # Need technicals — fetch indicators for the ~50-200 names FMP returned
        return _enrich_with_technicals(
            fmp_rows, rsi_min=rsi_min, rsi_max=rsi_max,
            above_sma_50=above_sma_50, above_sma_200=above_sma_200,
            sort_by=sort_by, limit=limit, universe_label=universe_label,
            n_universe=len(universe_rows) if not tickers else len(tickers),
            notices=notices, sources_used=["fmp", "tiingo"],
            filters=_filter_dict(sector, market_cap_min, market_cap_max, pe_min, pe_max,
                                  rsi_min, rsi_max, above_sma_50, above_sma_200),
        )

    # FMP unavailable — note why and continue to Tiingo fallback
    if fmp_status == "no_key":
        pass  # silent — FMP just isn't configured
    elif fmp_status == "rate_limited":
        notices.add("FMP daily quota exhausted (250/day). Falling back to Tiingo per-ticker.")
    elif fmp_status:
        notices.add(f"FMP unavailable ({fmp_status}). Falling back to Tiingo per-ticker.")

    # === Tiingo fallback path ===
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
    # `notices` already exists from the FMP fast-path block above; reuse it.

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
        "sources_used": ["tiingo"],
    }


def _filter_dict(sector, mcm, mcx, pem, pex, rsim, rsix, sma50, sma200) -> dict:
    """Stable filters block for the response. Same shape regardless of path."""
    return {
        "sector": sector,
        "market_cap_min": mcm,
        "market_cap_max": mcx,
        "pe_min": pem,
        "pe_max": pex,
        "rsi_min": rsim,
        "rsi_max": rsix,
        "above_sma_50": sma50,
        "above_sma_200": sma200,
    }


def _enrich_with_technicals(
    rows: list[dict],
    *,
    rsi_min: float | None,
    rsi_max: float | None,
    above_sma_50: bool | None,
    above_sma_200: bool | None,
    sort_by: str,
    limit: int,
    universe_label: str,
    n_universe: int,
    notices: set[str],
    sources_used: list[str],
    filters: dict,
) -> dict:
    """Enrich FMP-screened rows with Tiingo-computed technicals (RSI, SMA flags),
    apply technical filters, sort, and shape the final response."""
    def _ind_for(row: dict) -> tuple[dict, dict | None]:
        try:
            ind = _compute_indicators(row["ticker"], lookback_days=365)
            return (row, ind)
        except Exception:
            return (row, None)

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_ind_for, rows))

    matches: list[dict] = []
    for row, ind in results:
        if ind is None or "error" in ind:
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
        record = dict(row)
        record["rsi_14"] = rsi
        record["above_sma_50"] = ind["trend"]["above_sma_50"]
        record["above_sma_200"] = ind["trend"]["above_sma_200"]
        matches.append(record)

    sort_key_map = {"market_cap": ("market_cap", True), "pe": ("pe", False), "rsi": ("rsi_14", False)}
    sort_key, descending = sort_key_map.get(sort_by, ("market_cap", True))
    matches.sort(
        key=lambda m: (m.get(sort_key) is None, -(m.get(sort_key) or 0) if descending else (m.get(sort_key) or 0))
    )

    return {
        "universe": universe_label,
        "n_screened": len(rows),
        "n_universe": n_universe,
        "n_matches": len(matches),
        "n_returned": min(len(matches), limit),
        "filters": filters,
        "sort_by": sort_by,
        "matches": matches[:limit],
        "skipped_count": 0,
        "notices": sorted(notices),
        "sources_used": sources_used,
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
