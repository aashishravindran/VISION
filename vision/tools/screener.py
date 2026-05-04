"""Stock screener backed by FMP's server-side `/company-screener`.

One API call returns ranked, filtered results — what we used to need 50-500
per-ticker calls for. Supports sector AND industry filtering — `industry="Gold"`
or `industry="Other Precious Metals"` unlocks thematic queries.

Technical filters (RSI, SMA trend) are still computed locally via the `ta`
library on FMP price history — neither free tier exposes pre-computed RSI.
"""
from concurrent.futures import ThreadPoolExecutor

from agents import function_tool

from vision.data import fmp
from vision.tools.indicators import _compute_indicators
from vision.tools.universes import get_universe


def _filter_dict(sector, industry, mcm, mcx, pem, pex, rsim, rsix, sma50, sma200) -> dict:
    return {
        "sector": sector, "industry": industry,
        "market_cap_min": mcm, "market_cap_max": mcx,
        "pe_min": pem, "pe_max": pex,
        "rsi_min": rsim, "rsi_max": rsix,
        "above_sma_50": sma50, "above_sma_200": sma200,
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
    label: str,
    n_universe: int,
    notices: set[str],
    filters: dict,
) -> dict:
    """Compute RSI/SMA per ticker (parallel) and apply technical filters."""
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
        rec = dict(row)
        rec["rsi_14"] = rsi
        rec["above_sma_50"] = ind["trend"]["above_sma_50"]
        rec["above_sma_200"] = ind["trend"]["above_sma_200"]
        matches.append(rec)

    sort_key_map = {"market_cap": ("market_cap", True), "pe": ("pe", False), "rsi": ("rsi_14", False)}
    sort_key, descending = sort_key_map.get(sort_by, ("market_cap", True))
    matches.sort(
        key=lambda m: (m.get(sort_key) is None, -(m.get(sort_key) or 0) if descending else (m.get(sort_key) or 0))
    )

    return {
        "universe": label,
        "n_screened": len(rows),
        "n_universe": n_universe,
        "n_matches": len(matches),
        "n_returned": min(len(matches), limit),
        "filters": filters,
        "sort_by": sort_by,
        "matches": matches[:limit],
        "skipped_count": 0,
        "notices": sorted(notices),
        "sources_used": ["fmp"],
    }


def _screen_stocks(
    universe: str = "sp500",
    tickers: list[str] | None = None,
    sector: str | None = None,
    industry: str | None = None,
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
    """Internal screener implementation. The agent-facing tool and API both
    call this directly to avoid round-tripping through the FunctionTool wrapper.
    """
    limit = min(limit, 200)
    notices: set[str] = set()
    needs_technicals = (
        not skip_technicals
        and (rsi_min is not None or rsi_max is not None
             or above_sma_50 is not None or above_sma_200 is not None
             or sort_by == "rsi")
    )
    filters = _filter_dict(sector, industry, market_cap_min, market_cap_max,
                           pe_min, pe_max, rsi_min, rsi_max, above_sma_50, above_sma_200)

    # Universe membership filter — applied when explicitly scoped:
    # - `tickers=[...]` → exactly those names
    # - `industry=X` → no membership filter (gold miners etc. aren't all in
    #   S&P 500; thematic screens want the full FMP universe matching the
    #   industry, not the intersection with sp500)
    # - `universe="sp500|nasdaq100|dow30"` AND no industry → that index
    universe_filter: list[str] | None = None
    n_universe = 0
    if tickers:
        universe_filter = [t.upper() for t in tickers]
        n_universe = len(universe_filter)
        label = f"custom ({len(tickers)} tickers)"
    elif industry:
        # Thematic screen — let FMP return all matching the industry
        label = f"{industry} (all FMP names)"
    elif universe in ("sp500", "nasdaq100", "dow30"):
        try:
            universe_rows = get_universe(universe)
            universe_filter = [r["ticker"] for r in universe_rows]
            n_universe = len(universe_rows)
            label = universe
        except Exception:
            label = universe
    else:
        label = universe

    # === FMP screener — single API call ===
    try:
        fmp_rows = fmp.screen(
            sector=sector,
            industry=industry,
            market_cap_min=market_cap_min,
            market_cap_max=market_cap_max,
            pe_min=pe_min,
            pe_max=pe_max,
            limit=max(limit * 2, 100),
        )
    except fmp.FMPNoKeyError:
        return {
            "universe": label, "n_universe": n_universe,
            "n_screened": 0, "n_matches": 0, "n_returned": 0,
            "filters": filters, "sort_by": sort_by, "matches": [],
            "notices": ["FMP_API_KEY not set on the server. Add it to .env and restart."],
            "sources_used": [],
        }
    except fmp.FMPRateLimitError as e:
        return {
            "universe": label, "n_universe": n_universe,
            "n_screened": 0, "n_matches": 0, "n_returned": 0,
            "filters": filters, "sort_by": sort_by, "matches": [],
            "notices": [str(e)],
            "sources_used": [],
        }
    except fmp.FMPError as e:
        return {
            "universe": label, "n_universe": n_universe,
            "n_screened": 0, "n_matches": 0, "n_returned": 0,
            "filters": filters, "sort_by": sort_by, "matches": [],
            "notices": [f"FMP error: {e}"],
            "sources_used": [],
        }

    # Translate FMP rows into our internal record shape, applying universe filter
    universe_set = set(universe_filter) if universe_filter else None
    rows: list[dict] = []
    for r in fmp_rows:
        sym = (r.get("symbol") or "").upper()
        if not sym:
            continue
        if universe_set and sym not in universe_set:
            continue
        rows.append({
            "ticker": sym,
            "name": r.get("companyName"),
            "sector": r.get("sector"),
            "industry": r.get("industry"),
            "market_cap": r.get("marketCap"),
            "pe": None,  # FMP screener doesn't return P/E directly
            "pb": None,
            "price": r.get("price"),
            "beta": r.get("beta"),
            "volume": r.get("volume"),
            "exchange": r.get("exchangeShortName") or r.get("exchange"),
            "_source": "fmp",
        })

    # If an industry/sector filter returned 0 matches, FMP free might be
    # gating those tickers behind a paid tier (especially mining, commodities,
    # international). Surface this so the user understands.
    if not rows and (industry or sector):
        gated_hint = (
            f"FMP returned 0 matches for {'industry' if industry else 'sector'}="
            f"'{industry or sector}'. The free tier gates many tickers — "
            "mining, commodities, and most international names are typically "
            "behind a paid plan. Try a sector/industry FMP free covers fully "
            "(Technology, Energy, Healthcare, Financial Services, Software - "
            "Application, Semiconductors, Banks - Diversified)."
        )
        notices.add(gated_hint)

    if not needs_technicals:
        rows.sort(
            key=lambda m: (m.get("market_cap") is None, -(m.get("market_cap") or 0))
            if sort_by == "market_cap"
            else (m.get(sort_by) is None, -(m.get(sort_by) or 0))
        )
        return {
            "universe": label,
            "n_screened": len(rows),
            "n_universe": n_universe,
            "n_matches": len(rows),
            "n_returned": min(len(rows), limit),
            "filters": filters,
            "sort_by": sort_by,
            "matches": rows[:limit],
            "skipped_count": 0,
            "notices": sorted(notices),
            "sources_used": ["fmp"],
        }

    # Technical filtering needs per-ticker price history → FMP /historical-price-eod
    # For top ~50-200 names this is cheap.
    return _enrich_with_technicals(
        rows[:max(limit * 2, 100)],
        rsi_min=rsi_min, rsi_max=rsi_max,
        above_sma_50=above_sma_50, above_sma_200=above_sma_200,
        sort_by=sort_by, limit=limit, label=label,
        n_universe=n_universe, notices=notices, filters=filters,
    )


@function_tool
def screen_stocks(
    universe: str = "sp500",
    tickers: list[str] | None = None,
    sector: str | None = None,
    industry: str | None = None,
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

    Universe options:
    - "sp500" / "nasdaq100" / "dow30" — named index (default sp500)
    - `tickers=[...]` — explicit ticker list (overrides `universe`)

    For thematic queries ("precious metals", "AI semiconductors", "cybersecurity"),
    use the `industry` filter — FMP supports ~150 specific industry classifications
    including: Gold, Silver, Other Precious Metals, Copper, Steel, Semiconductors,
    Software - Application, Software - Infrastructure, Information Technology
    Services, Drug Manufacturers - General, Banks - Diversified, Oil & Gas E&P,
    Solar, Aerospace & Defense, etc.

    Filters AND-combine. Pass only what you need.

    Args:
        universe: "sp500", "nasdaq100", "dow30". Ignored if `tickers` provided.
        tickers: Explicit ticker list (overrides `universe`).
        sector: GICS sector — "Technology", "Energy", "Financial Services", etc.
        industry: GICS industry — see list above. Use this for theme-driven queries.
        market_cap_min: Minimum market cap in USD (e.g., 10_000_000_000 = $10B).
        market_cap_max: Maximum market cap in USD.
        pe_min: Minimum trailing P/E ratio.
        pe_max: Maximum trailing P/E ratio.
        rsi_min: Minimum RSI(14). Use 70 for overbought.
        rsi_max: Maximum RSI(14). Use 30 for oversold.
        above_sma_50: If True, only names trading above their 50d SMA.
        above_sma_200: If True, only names trading above their 200d SMA.
        sort_by: "market_cap" (default), "pe", or "rsi".
        limit: Max results. Default 50, max 200.
        skip_technicals: If True, skip RSI/SMA fetch (much faster, fundamentals only).
    """
    return _screen_stocks(
        universe=universe, tickers=tickers, sector=sector, industry=industry,
        market_cap_min=market_cap_min, market_cap_max=market_cap_max,
        pe_min=pe_min, pe_max=pe_max,
        rsi_min=rsi_min, rsi_max=rsi_max,
        above_sma_50=above_sma_50, above_sma_200=above_sma_200,
        sort_by=sort_by, limit=limit, skip_technicals=skip_technicals,
    )
