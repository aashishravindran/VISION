"""Stock universe ticker lists. Scraped from Wikipedia and cached locally.

Universes:
- sp500   — S&P 500 (~503 tickers; updates with index changes)
- nasdaq100 — Nasdaq 100 (~100 tickers)
- dow30   — Dow Jones Industrial Average (30 tickers)
"""
import io

import httpx
import pandas as pd

from vision import cache

WIKI_URLS = {
    "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "nasdaq100": "https://en.wikipedia.org/wiki/Nasdaq-100",
    "dow30": "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average",
}

# Wikipedia table indexes per universe
WIKI_TABLE = {
    "sp500": 0,
    "nasdaq100": 4,  # "Components" table
    "dow30": 2,
}

WIKI_TICKER_COL = {
    "sp500": "Symbol",
    "nasdaq100": "Ticker",
    "dow30": "Symbol",
}


def _fetch_wikipedia(name: str) -> list[dict]:
    url = WIKI_URLS[name]
    headers = {"User-Agent": "VISION/0.1 (research)"}
    resp = httpx.get(url, headers=headers, timeout=30.0)
    resp.raise_for_status()
    # pandas 3.0 requires a file-like object — wrap the HTML string in StringIO
    tables = pd.read_html(io.StringIO(resp.text))

    # Some universes' tables shift around; find the first table that has the ticker column
    ticker_col = WIKI_TICKER_COL[name]
    df = None
    for t in tables:
        if ticker_col in t.columns:
            df = t
            break
    if df is None:
        df = tables[WIKI_TABLE[name]]

    rows = []
    for _, r in df.iterrows():
        ticker = str(r.get(ticker_col, "")).strip().upper().replace(".", "-")
        if not ticker or ticker == "NAN":
            continue
        rows.append({
            "ticker": ticker,
            "name": str(r.get("Security") or r.get("Company") or r.get("Issuer") or ""),
            "sector": str(r.get("GICS Sector") or r.get("Sector") or ""),
            "industry": str(r.get("GICS Sub-Industry") or r.get("Industry") or ""),
        })
    return rows


def get_universe(name: str) -> list[dict]:
    """Return [{ticker, name, sector, industry}, ...] for a named universe.

    Cached for 7 days (universes only change with quarterly index reconstitution).
    """
    name = name.lower()
    if name not in WIKI_URLS:
        raise ValueError(f"unknown universe: {name}. Choose from {list(WIKI_URLS)}")
    params = {"name": name}
    cached = cache.get("get_universe", params, ttl_hours=24 * 7)
    if cached is not None:
        return cached
    rows = _fetch_wikipedia(name)
    cache.put("get_universe", params, rows)
    return rows
