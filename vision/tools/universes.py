"""Stock universe ticker lists, sourced from FMP.

Universes:
- sp500     — S&P 500 (~503 tickers)
- nasdaq100 — Nasdaq 100 (~100 tickers)
- dow30     — Dow Jones Industrial Average (30 tickers)

FMP's `/sp500-constituent` etc. give us symbol + name + sector + sub-industry
in one call, cached 7 days. This replaces the Wikipedia scrape we had before.
"""
from vision.data import fmp


def get_universe(name: str) -> list[dict]:
    """Return [{ticker, name, sector, industry}, ...] for a named universe."""
    rows = fmp.constituents(name)
    return [
        {
            "ticker": r.get("symbol"),
            "name": r.get("name"),
            "sector": r.get("sector"),
            "industry": r.get("subSector") or r.get("industry"),
        }
        for r in rows
        if r.get("symbol")
    ]
