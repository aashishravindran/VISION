"""Chart data builders. Returns OHLCV + computed indicators in a Plotly-ready
shape. The frontend renders the actual treemap/candlestick — we just hand off
arrays.

Indicators computed: SMA(20/50/200), EMA(20), Bollinger Bands(20,2),
RSI(14), MACD(12,26,9). Returned in subplot-ready slices so the frontend
doesn't need to transform anything.
"""
from __future__ import annotations

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, SMAIndicator
from ta.volatility import BollingerBands

from vision import cache
from vision.data import tiingo
from vision.data.tiingo import TiingoRateLimitError, TiingoTierLimitError


def _to_series_floats(s: pd.Series) -> list[float | None]:
    """Convert a pandas Series to JSON-friendly list (None for NaN)."""
    out: list[float | None] = []
    for v in s.tolist():
        try:
            if v is None or (isinstance(v, float) and pd.isna(v)):
                out.append(None)
            else:
                out.append(round(float(v), 4))
        except (TypeError, ValueError):
            out.append(None)
    return out


def get_chart(ticker: str, lookback_days: int = 365, indicators: list[str] | None = None) -> dict:
    """Return chart data for a ticker: OHLCV bars + selected indicator overlays.

    Args:
        ticker: Symbol.
        lookback_days: Window size; default 365 (~1 trading year, enough for SMA200).
        indicators: Subset of {"sma", "ema", "bb", "rsi", "macd"}. Defaults to all.

    Returned shape (snake_case, JSON-ready):
        {
            "ticker": "...",
            "as_of": "YYYY-MM-DD",
            "n": int,
            "dates": [...], "open": [...], "high": [...], "low": [...],
            "close": [...], "volume": [...],
            "overlays": {                    # plotted on the price subplot
                "sma_20": [...], "sma_50": [...], "sma_200": [...],
                "ema_20": [...],
                "bb_upper": [...], "bb_middle": [...], "bb_lower": [...]
            },
            "subpanels": {                   # each on its own subplot
                "rsi_14": [...],
                "macd": [...], "macd_signal": [...], "macd_hist": [...]
            },
            "summary": {                     # latest readings
                "price": float, "rsi_14": float, "above_sma_50": bool,
                "above_sma_200": bool, "golden_cross": bool
            }
        }
    """
    requested = set(indicators) if indicators else {"sma", "ema", "bb", "rsi", "macd"}
    cache_params = {"ticker": ticker.upper(), "lookback_days": lookback_days, "ind": sorted(requested)}
    cached = cache.get("get_chart", cache_params, ttl_hours=24)
    if cached is not None:
        return cached

    # Use the raw fetcher so we can distinguish "no data" from "rate-limited".
    try:
        rows = tiingo._fetch_prices(ticker, lookback_days) or []
        rate_limited = False
    except TiingoRateLimitError:
        rows = []
        rate_limited = True
    except TiingoTierLimitError as e:
        return {"ticker": ticker.upper(), "error": "tier_limited", "error_message": str(e)}
    except Exception as e:
        return {"ticker": ticker.upper(), "error": "fetch_failed", "error_message": str(e)}

    if not rows:
        if rate_limited:
            return {
                "ticker": ticker.upper(),
                "error": "rate_limited",
                "error_message": (
                    "Tiingo daily request limit reached. Resets at 00:00 UTC. "
                    "Cached charts you've viewed today still load."
                ),
            }
        return {"ticker": ticker.upper(), "error": "no_data", "error_message": "No price data from Tiingo for this ticker."}

    df = pd.DataFrame([
        {
            "Date": r.get("date"),
            "Open": r.get("open"),
            "High": r.get("high"),
            "Low": r.get("low"),
            "Close": r.get("adjClose") or r.get("close"),
            "Volume": r.get("volume"),
        }
        for r in rows
    ])
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.dropna(subset=["Close"]).sort_values("Date").reset_index(drop=True)
    if df.empty:
        return {"ticker": ticker.upper(), "error": "Price series empty after cleaning."}

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)

    overlays: dict[str, list[float | None]] = {}
    subpanels: dict[str, list[float | None]] = {}

    if "sma" in requested:
        overlays["sma_20"] = _to_series_floats(SMAIndicator(close=close, window=20).sma_indicator())
        overlays["sma_50"] = _to_series_floats(SMAIndicator(close=close, window=50).sma_indicator())
        overlays["sma_200"] = _to_series_floats(SMAIndicator(close=close, window=200).sma_indicator())
    if "ema" in requested:
        overlays["ema_20"] = _to_series_floats(EMAIndicator(close=close, window=20).ema_indicator())
    if "bb" in requested:
        bb = BollingerBands(close=close, window=20, window_dev=2)
        overlays["bb_upper"] = _to_series_floats(bb.bollinger_hband())
        overlays["bb_middle"] = _to_series_floats(bb.bollinger_mavg())
        overlays["bb_lower"] = _to_series_floats(bb.bollinger_lband())
    if "rsi" in requested:
        subpanels["rsi_14"] = _to_series_floats(RSIIndicator(close=close, window=14).rsi())
    if "macd" in requested:
        m = MACD(close=close, window_fast=12, window_slow=26, window_sign=9)
        subpanels["macd"] = _to_series_floats(m.macd())
        subpanels["macd_signal"] = _to_series_floats(m.macd_signal())
        subpanels["macd_hist"] = _to_series_floats(m.macd_diff())

    last_close = float(close.iloc[-1])
    sma_50_last = overlays.get("sma_50", [None])[-1] if "sma" in requested else None
    sma_200_last = overlays.get("sma_200", [None])[-1] if "sma" in requested else None
    rsi_last = subpanels.get("rsi_14", [None])[-1] if "rsi" in requested else None

    out = {
        "ticker": ticker.upper(),
        "as_of": df["Date"].iloc[-1].strftime("%Y-%m-%d"),
        "n": len(df),
        "dates": df["Date"].dt.strftime("%Y-%m-%d").tolist(),
        "open": _to_series_floats(df["Open"]),
        "high": _to_series_floats(df["High"]),
        "low": _to_series_floats(df["Low"]),
        "close": _to_series_floats(close),
        "volume": _to_series_floats(df["Volume"].astype(float)),
        "overlays": overlays,
        "subpanels": subpanels,
        "summary": {
            "price": round(last_close, 4),
            "rsi_14": rsi_last,
            "above_sma_50": (
                sma_50_last is not None and last_close > sma_50_last
            ),
            "above_sma_200": (
                sma_200_last is not None and last_close > sma_200_last
            ),
            "golden_cross": (
                sma_50_last is not None and sma_200_last is not None and sma_50_last > sma_200_last
            ),
        },
    }
    cache.put("get_chart", cache_params, out)
    return out
