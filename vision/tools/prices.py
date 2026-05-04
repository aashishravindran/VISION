"""Quote and price-history tools backed by FMP."""
from agents import function_tool

from vision.data import fmp


@function_tool
def get_quote(ticker: str) -> dict:
    """Get a quote-style snapshot for a ticker: latest price, market cap,
    valuation metrics (P/E), recent change, asset metadata.

    Use as a first-look summary of a stock before drilling into fundamentals
    or technicals.

    Args:
        ticker: Symbol, e.g. "NVDA".
    """
    try:
        q = fmp.single_quote(ticker)
        prof = fmp.profile(ticker)
    except fmp.FMPNoKeyError:
        return {"ticker": ticker.upper(), "error": "no_key",
                "error_message": "FMP_API_KEY not set on the server."}
    except fmp.FMPTierGatedError as e:
        return {"ticker": ticker.upper(), "error": "tier_gated",
                "error_message": (
                    f"FMP free tier does not cover {ticker.upper()}. "
                    f"Try lookup_ticker_via_web for this ticker — it pulls live data "
                    f"from public sources and works for FMP-gated names like mining, "
                    f"commodities, and many international tickers."
                )}
    except fmp.FMPRateLimitError as e:
        return {"ticker": ticker.upper(), "error": "rate_limited", "error_message": str(e)}
    except fmp.FMPError as e:
        return {"ticker": ticker.upper(), "error": "fmp_error", "error_message": str(e)}

    if not q and not prof:
        return {"ticker": ticker.upper(), "error": "No data returned for this ticker."}

    q = q or {}
    prof = prof or {}
    return {
        "ticker": ticker.upper(),
        "name": q.get("name") or prof.get("companyName"),
        "exchange": q.get("exchange") or prof.get("exchangeShortName"),
        "sector": prof.get("sector"),
        "industry": prof.get("industry"),
        "description": (prof.get("description") or "")[:500] or None,
        "country": prof.get("country"),
        "ceo": prof.get("ceo"),
        "employees": prof.get("fullTimeEmployees"),
        "ipo_date": prof.get("ipoDate"),
        "last_close": q.get("price"),
        "previous_close": q.get("previousClose"),
        "change_pct": q.get("changePercentage"),
        "day_low": q.get("dayLow"),
        "day_high": q.get("dayHigh"),
        "year_low": q.get("yearLow"),
        "year_high": q.get("yearHigh"),
        "price_avg_50d": q.get("priceAvg50"),
        "price_avg_200d": q.get("priceAvg200"),
        "volume": q.get("volume"),
        "avg_volume": q.get("avgVolume"),
        "market_cap": q.get("marketCap") or prof.get("marketCap"),
        "pe_ratio": q.get("pe"),
        "eps": q.get("eps"),
        "shares_outstanding": q.get("sharesOutstanding"),
        "earnings_announcement": q.get("earningsAnnouncement"),
        "beta": prof.get("beta"),
    }


@function_tool
def get_price_history(ticker: str, lookback_days: int = 252) -> dict:
    """Fetch end-of-day OHLCV price history for a ticker.

    Args:
        ticker: Symbol.
        lookback_days: How many calendar days. Default 252 (~1 trading year).
    """
    try:
        rows = fmp.historical_prices(ticker, lookback_days)
    except fmp.FMPNoKeyError:
        return {"ticker": ticker.upper(), "error": "no_key",
                "error_message": "FMP_API_KEY not set on the server."}
    except fmp.FMPTierGatedError:
        return {"ticker": ticker.upper(), "error": "tier_gated",
                "error_message": (
                    f"FMP free tier does not cover historical prices for "
                    f"{ticker.upper()}. Try lookup_ticker_via_web for context."
                )}
    except fmp.FMPRateLimitError as e:
        return {"ticker": ticker.upper(), "error": "rate_limited", "error_message": str(e)}
    except fmp.FMPError as e:
        return {"ticker": ticker.upper(), "error": "fmp_error", "error_message": str(e)}

    if not rows:
        return {"ticker": ticker.upper(), "error": "No price history available."}

    # Slim to the OHLCV fields we actually use; drop FMP's noisy extras.
    slim = []
    for r in rows:
        slim.append({
            "date": r.get("date"),
            "open": r.get("open"),
            "high": r.get("high"),
            "low": r.get("low"),
            "close": r.get("close"),
            "adj_close": r.get("adjClose") or r.get("close"),
            "volume": r.get("volume"),
        })
    return {
        "ticker": ticker.upper(),
        "n": len(slim),
        "start": slim[0]["date"],
        "end": slim[-1]["date"],
        "rows": slim,
    }
