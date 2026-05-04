"""Server-side chart rendering for the vision pass.

Renders a candlestick + indicator panel PNG using mplfinance/matplotlib so
GPT-5 can look at the chart with vision. Cached aggressively because PNG
generation is the slowest step in the vision tool's hot path.
"""
from __future__ import annotations

import base64
import io

import matplotlib

# Headless backend — no display server in FastAPI's worker.
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator

from vision import cache
from vision.data import fmp


def render_chart_png(ticker: str, lookback_days: int = 252) -> bytes | None:
    """Render a candlestick chart with SMA(20/50/200) overlay and RSI(14)
    subpanel as PNG bytes. Returns None if no price data available.

    Cached for 12h (PNG bytes stored as base64 in cache)."""
    params = {"ticker": ticker.upper(), "lookback_days": lookback_days}
    cached = cache.get("chart_png", params, ttl_hours=12)
    if cached is not None:
        b64 = cached.get("png_b64")
        if b64:
            return base64.b64decode(b64)

    try:
        rows = fmp.historical_prices(ticker, lookback_days)
    except fmp.FMPError:
        return None
    if not rows:
        return None

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
    df = df.dropna(subset=["Close"]).sort_values("Date").set_index("Date")
    if df.empty:
        return None

    close = df["Close"].astype(float)
    sma_20 = SMAIndicator(close=close, window=20).sma_indicator()
    sma_50 = SMAIndicator(close=close, window=50).sma_indicator()
    sma_200 = SMAIndicator(close=close, window=200).sma_indicator()
    rsi = RSIIndicator(close=close, window=14).rsi()

    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=mpf.make_marketcolors(
            up="#10b981", down="#ef4444",
            edge={"up": "#10b981", "down": "#ef4444"},
            wick={"up": "#10b981", "down": "#ef4444"},
            volume="in",
        ),
        facecolor="#0b0d12",
        edgecolor="#222632",
        figcolor="#0b0d12",
        gridcolor="#222632",
        rc={"axes.labelcolor": "#e6e9f0", "ytick.color": "#e6e9f0", "xtick.color": "#e6e9f0"},
    )

    addplots = [
        mpf.make_addplot(sma_20, color="#7aa2f7", width=1.0),
        mpf.make_addplot(sma_50, color="#f59e0b", width=1.0),
        mpf.make_addplot(sma_200, color="#a78bfa", width=1.0),
        mpf.make_addplot(rsi, panel=2, color="#a78bfa", width=1.0, ylabel="RSI(14)"),
    ]

    buf = io.BytesIO()
    mpf.plot(
        df,
        type="candle",
        style=style,
        addplot=addplots,
        volume=True,
        figsize=(12, 7),
        panel_ratios=(6, 2, 2),
        title=f"\n{ticker.upper()}  (last {len(df)}d)",
        ylabel="Price",
        ylabel_lower="Volume",
        savefig=dict(fname=buf, format="png", dpi=110, bbox_inches="tight"),
    )
    plt.close("all")
    png = buf.getvalue()

    cache.put("chart_png", params, {"png_b64": base64.b64encode(png).decode()})
    return png
