"""Sector ETF performance backed by FMP.

We fetch per-ETF historical prices (parallel) so we get proper 1d/1w/1m/3m/YTD
returns instead of just the 1d change that batch_quote provides. With our 24h
cache TTL on historical prices, this is one round of FMP calls per day per
session — cheap.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from agents import function_tool

from vision import cache
from vision.config import BENCHMARK_ETFS, SECTOR_ETFS
from vision.data import fmp


def _returns_from_history(rows: list[dict]) -> dict:
    """Given an FMP historical-price-eod payload (newest-first when raw, but we
    sort), compute 1d/1w/1m/3m/YTD returns. Returns dict with last_close + each
    period."""
    if not rows or len(rows) < 2:
        return {}
    # FMP historical-price-eod/full returns rows with 'date' + 'close' (and
    # 'adjClose' on some endpoints). Sort oldest→newest for indexing convenience.
    rows = sorted(rows, key=lambda r: r.get("date") or "")
    closes = [r.get("adjClose") or r.get("close") for r in rows]
    closes = [c for c in closes if c is not None]
    if len(closes) < 2:
        return {}

    last = closes[-1]

    def pct(days_back: int) -> float | None:
        if len(closes) <= days_back:
            return None
        prev = closes[-1 - days_back]
        if not prev:
            return None
        return round((last / prev - 1) * 100, 2)

    # YTD: find first close in the current calendar year
    end_date = rows[-1].get("date")
    ret_ytd = None
    if end_date:
        year = end_date[:4]
        ytd_rows = [r for r in rows if (r.get("date") or "").startswith(year)]
        if ytd_rows:
            first = ytd_rows[0].get("adjClose") or ytd_rows[0].get("close")
            if first:
                ret_ytd = round((last / first - 1) * 100, 2)

    return {
        "last_close": round(float(last), 2),
        "as_of": end_date,
        "ret_1d": pct(1),
        "ret_1w": pct(5),    # 5 trading days
        "ret_1m": pct(21),   # ~21 trading days
        "ret_3m": pct(63),
        "ret_ytd": ret_ytd,
    }


@function_tool
def get_sector_performance(lookback_days: int = 365) -> dict:
    """Get returns for the 11 SPDR sector ETFs and key benchmarks (SPY/QQQ/
    IWM/DIA).

    Returns 1-day, 1-week, 1-month, 3-month, and YTD returns derived from
    per-ETF historical prices. Use to identify sector rotation, leadership
    shifts, and overall market regime.

    What this tool does NOT provide:
    - ETF money flows (creations/redemptions) — not available on FMP free tier
    - Industry-level (sub-sector) returns — use the screener specialist with
      `industry=...` for those
    - Intraday data — EOD only

    Args:
        lookback_days: History window for return calculation. Default 365
            (gets clean 1y reads + plenty of room for 3m/YTD).
    """
    cached = cache.get("get_sector_performance_v5", {"lookback": lookback_days}, ttl_hours=4)
    if cached is not None:
        return cached

    tickers = list(SECTOR_ETFS.keys()) + list(BENCHMARK_ETFS.keys())

    # Parallel per-ETF historical fetch. With FMP's 24h cache on historical
    # prices, this is essentially free after the first call of the day.
    def _fetch(t: str) -> tuple[str, dict | str]:
        try:
            rows = fmp.historical_prices(t, lookback_days)
            return t, _returns_from_history(rows)
        except fmp.FMPTierGatedError:
            return t, "tier_gated"
        except fmp.FMPRateLimitError:
            return t, "rate_limited"
        except fmp.FMPNoKeyError:
            return t, "no_key"
        except fmp.FMPError as e:
            return t, f"error: {e}"

    notices: set[str] = set()
    by_ticker: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for t, result in pool.map(_fetch, tickers):
            if isinstance(result, str):
                if result == "tier_gated":
                    notices.add(f"FMP free tier doesn't cover {t} (unexpected for SPDR ETFs).")
                elif result == "rate_limited":
                    notices.add("FMP daily quota exhausted — sector performance partial.")
                elif result == "no_key":
                    notices.add("FMP_API_KEY not set on the server.")
                else:
                    notices.add(f"{t}: {result}")
                continue
            by_ticker[t] = result

    if not by_ticker:
        return {
            "error": "all_failed",
            "error_message": "Could not fetch sector performance.",
            "notices": sorted(notices),
        }

    rows = []
    for t in tickers:
        r = by_ticker.get(t)
        if not r:
            continue
        rows.append({
            "ticker": t,
            "name": SECTOR_ETFS.get(t) or BENCHMARK_ETFS.get(t),
            "kind": "sector" if t in SECTOR_ETFS else "benchmark",
            "last_close": r.get("last_close"),
            "as_of": r.get("as_of"),
            "ret_1d_pct": r.get("ret_1d"),
            "ret_1w_pct": r.get("ret_1w"),
            "ret_1m_pct": r.get("ret_1m"),
            "ret_3m_pct": r.get("ret_3m"),
            "ret_ytd_pct": r.get("ret_ytd"),
        })

    rows.sort(key=lambda r: (r["kind"] != "sector", -(r["ret_1m_pct"] or 0)))
    out = {
        "source": "FMP /historical-price-eod (per-ETF, parallel)",
        "as_of": date.today().isoformat(),
        "sectors": [r for r in rows if r["kind"] == "sector"],
        "benchmarks": [r for r in rows if r["kind"] == "benchmark"],
    }
    if notices:
        out["notices"] = sorted(notices)
    cache.put("get_sector_performance_v5", {"lookback": lookback_days}, out)
    return out
