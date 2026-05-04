"""Chart vision tool — renders a chart as PNG and uses GPT-5 vision to
identify visual patterns the numerical indicators alone might miss.

Why a separate vision pass:
- Numerical indicators (RSI, MACD) describe the *current* state.
- Visual patterns (head-and-shoulders, triangles, breakouts, divergences)
  emerge from the *shape* of the price series and are what discretionary
  technical analysts look for.
- A vision-capable model genuinely sees the chart, not a description.

The tool calls the OpenAI Responses API directly with an image input and
returns the structured analysis as a string for the agent to quote.
"""
from __future__ import annotations

import base64
import os
from typing import Any

from agents import function_tool
from openai import OpenAI

from vision import cache
from vision.chart_render import render_chart_png

# Vision pass uses gpt-5-mini by default — fast, cheap, and good at chart
# pattern recognition. Override with VISION_VISION_MODEL if needed.
VISION_MODEL = os.environ.get("VISION_VISION_MODEL", "gpt-5-mini")

VISION_PROMPT = """You are a technical-analysis chartist. The image is a candlestick chart with SMA(20/50/200) overlays, volume, and an RSI(14) subpanel for {ticker}.

Identify any visually-evident patterns or signals. Be specific and concrete:
- Trend (uptrend, downtrend, sideways consolidation)
- Pattern (head-and-shoulders, double-top/bottom, triangle, flag, wedge, breakout, breakdown — only if visible)
- Support/resistance (specific price levels visible on the chart)
- MA structure (price vs SMAs, golden/death cross, MA fan)
- RSI behavior (extremes, divergences from price)
- Volume confirmation (or lack thereof) on key moves

Honesty rules:
- If a pattern is ambiguous, say "no clear pattern" — don't force one.
- If the chart looks unremarkable, say so.
- Cite specific dates / price levels you can see (e.g. "support around $145 from Feb 2025 lows").

Output: 4-8 bullet points, no preamble, no caveats about risk."""


@function_tool
def analyze_chart_visually(ticker: str, lookback_days: int = 252) -> dict:
    """Render the ticker's price chart as an image and have a vision-capable
    model identify visual patterns (breakouts, H&S, divergences, etc.) that
    pure numerical indicators miss.

    Use this AFTER `compute_indicators` for any ticker analysis where
    chart-pattern context would meaningfully add to the answer. Don't call
    it for sector/news/screener questions — only when the user wants per-
    ticker technical depth.

    Args:
        ticker: Stock symbol.
        lookback_days: Window size; default 252 (~1 trading year).

    Returns dict with `findings` (the vision analysis) and `chart_marker`
    (`[chart:TICKER]` for the orchestrator to embed in the final answer).
    """
    cache_key = {"ticker": ticker.upper(), "lookback_days": lookback_days}
    cached = cache.get("vision_analysis", cache_key, ttl_hours=12)
    if cached is not None:
        return cached

    png = render_chart_png(ticker, lookback_days)
    if png is None:
        return {
            "ticker": ticker.upper(),
            "error": "Could not render chart — no price data from Tiingo for this ticker.",
        }

    client = OpenAI()
    image_b64 = base64.b64encode(png).decode()
    image_url = f"data:image/png;base64,{image_b64}"

    try:
        response = client.responses.create(
            model=VISION_MODEL,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": VISION_PROMPT.format(ticker=ticker.upper()),
                        },
                        {"type": "input_image", "image_url": image_url, "detail": "high"},
                    ],
                }
            ],
        )
    except Exception as e:
        return {
            "ticker": ticker.upper(),
            "error": f"Vision call failed: {e}",
        }

    # Extract the final text from the response
    findings = ""
    output: Any = getattr(response, "output", None) or []
    for item in output:
        item_type = getattr(item, "type", None)
        if item_type != "message":
            continue
        for c in getattr(item, "content", None) or []:
            if getattr(c, "type", None) == "output_text":
                findings += getattr(c, "text", "") or ""

    if not findings:
        findings = (getattr(response, "output_text", "") or "").strip() or "(vision returned no text)"

    out = {
        "ticker": ticker.upper(),
        "lookback_days": lookback_days,
        "findings": findings.strip(),
        "chart_marker": f"[chart:{ticker.upper()}]",
        "model": VISION_MODEL,
    }
    cache.put("vision_analysis", cache_key, out)
    return out
