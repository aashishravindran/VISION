"""Quote and price-history tools backed by Tiingo."""
from agents import function_tool

from vision.data import tiingo


@function_tool
def get_quote(ticker: str) -> dict:
    """Get a quote-style snapshot for a ticker: latest price, market cap,
    valuation metrics (P/E, P/B, PEG), recent returns, and asset metadata.

    Use this as a first-look summary of a stock before drilling into
    fundamentals or technicals.

    Args:
        ticker: Symbol, e.g. "NVDA".
    """
    metrics = tiingo.get_daily_metrics(ticker)
    summary = tiingo.get_price_summary(ticker, lookback_days=300)
    meta = tiingo.get_meta(ticker) or {}

    metric_error = metrics.get("error")  # tier_limited / rate_limited / no_data / fetch_failed
    if summary.last_close is None and not meta and metric_error:
        return {
            "ticker": ticker.upper(),
            "error": metrics.get("error_message")
            or "No data returned from Tiingo. Check the ticker symbol or your TIINGO_API_KEY.",
        }

    out = {
        "ticker": ticker.upper(),
        "name": meta.get("name"),
        "exchange": meta.get("exchangeCode"),
        "description": (meta.get("description") or "")[:500] or None,
        "last_close": summary.last_close,
        "as_of": summary.as_of or metrics.get("as_of"),
        "ret_1d_pct": summary.ret_1d_pct,
        "ret_1w_pct": summary.ret_1w_pct,
        "ret_1m_pct": summary.ret_1m_pct,
        "ret_3m_pct": summary.ret_3m_pct,
        "ret_ytd_pct": summary.ret_ytd_pct,
        "market_cap": metrics.get("market_cap"),
        "enterprise_value": metrics.get("enterprise_value"),
        "pe_ratio": metrics.get("pe_ratio"),
        "pb_ratio": metrics.get("pb_ratio"),
        "peg_ratio_1y": metrics.get("peg_ratio_1y"),
    }
    if metric_error:
        out["fundamentals_status"] = metric_error
        out["fundamentals_message"] = metrics.get("error_message")
    return out


@function_tool
def get_price_history(ticker: str, lookback_days: int = 252) -> dict:
    """Fetch end-of-day OHLCV price history for a ticker.

    Args:
        ticker: Symbol.
        lookback_days: How many calendar days. Default 252 (~1 trading year).
    """
    rows = tiingo.get_price_history(ticker, lookback_days)
    if not rows:
        return {"ticker": ticker.upper(), "error": "No price history available."}
    # Slim each row to OHLCV fields
    out_rows = []
    for r in rows:
        out_rows.append({
            "date": (r.get("date") or "")[:10],
            "open": r.get("open"),
            "high": r.get("high"),
            "low": r.get("low"),
            "close": r.get("close"),
            "adj_close": r.get("adjClose"),
            "volume": r.get("volume"),
        })
    return {
        "ticker": ticker.upper(),
        "n": len(out_rows),
        "start": out_rows[0]["date"] if out_rows else None,
        "end": out_rows[-1]["date"] if out_rows else None,
        "rows": out_rows,
    }
