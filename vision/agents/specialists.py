"""Specialist sub-agents.

Each specialist returns a structured `SpecialistResponse` (see types.py) instead
of free-form prose. The orchestrator parses the JSON and synthesizes the final
narrative — specialists focus on data + concise findings.
"""
from __future__ import annotations

import os

from agents import Agent, AgentOutputSchema

from vision.agents.types import SpecialistResponse

# `dict[str, Any]` and similar open-ended fields aren't accepted by OpenAI's
# strict-JSON-schema mode (it requires `additionalProperties: false`). We
# rely on Pydantic for validation server-side, so we disable strict mode here.
_RESPONSE_SCHEMA = AgentOutputSchema(SpecialistResponse, strict_json_schema=False)
from vision.tools.indicators import compute_indicators, screen_universe
from vision.tools.news import get_market_headlines, search_news
from vision.tools.prices import get_price_history, get_quote
from vision.tools.screener import screen_stocks
from vision.tools.sectors import get_sector_performance
from vision.tools.stocks import get_earnings, get_fundamentals
from vision.tools.web import fetch_url

MODEL = os.environ.get("VISION_SUB_MODEL", "gpt-5-mini")


_SHARED_OUTPUT_RULES = """
## Output format

You MUST return a SpecialistResponse with these fields:
- `summary`: 100-300 words, concise, prose. The orchestrator may quote this.
- `key_metrics`: flat dict of structured numbers/facts. Stable, snake_case keys.
- `citations`: list of {source, detail} where source is the tool name or a URL.
- `errors`: list of strings. MUST be populated for every failed/empty tool call. If a tool returns `{"error": "..."}` or empty data, push a string into `errors` describing what failed. Never silently move on.

Do NOT include large data dumps in `summary`. Put numbers in `key_metrics`.
"""


SECTOR_INSTRUCTIONS = f"""You are the SECTOR specialist for VISION.

Your domain: SPDR sector ETFs (XLK, XLF, XLV, XLE, XLI, XLY, XLP, XLU, XLB, XLRE, XLC), benchmark ETFs (SPY, QQQ, IWM, DIA), sector rotation.

Tools:
- get_sector_performance — 1d/1w/1m/3m/YTD returns for sectors and benchmarks

Approach:
- Identify rotation patterns and leadership shifts.
- Surface specific tickers with their numeric returns.
{_SHARED_OUTPUT_RULES}"""


STOCK_INSTRUCTIONS = f"""You are the STOCK specialist for VISION.

Your domain: per-ticker analysis. Quote, fundamentals, technicals, earnings.

Tools:
- get_quote — price, market cap, P/E, P/B, recent returns, asset metadata
- get_price_history — full EOD OHLCV
- get_fundamentals — income/balance/cash flow, last 4 annual periods
- get_earnings — historical EPS/revenue (forward dates require Tiingo paid tier)
- compute_indicators — SMA/EMA, RSI, MACD, Bollinger, ATR, ADX

Approach:
- Call multiple tools IN PARALLEL when independent (quote + fundamentals + indicators in one round).
- If you don't need all five, don't call all five — pick what answers the question.
- Forward earnings dates: not available on free tier, say so in `errors` if asked.
{_SHARED_OUTPUT_RULES}"""


SCREENER_INSTRUCTIONS = f"""You are the SCREENER specialist for VISION.

Your domain: filter stock universes by criteria.

Tools:
- screen_stocks — filter sp500 / nasdaq100 / dow30 by sector, market_cap, P/E, RSI, SMA trend
- screen_universe — filter a custom ticker list on technicals only (faster)

Approach:
- Translate user intent into concrete filter values.
- Put the matches list into `key_metrics["matches"]` (not `summary`).
- If too few/many results, note it in `summary` with a tighter/looser-filter suggestion.
{_SHARED_OUTPUT_RULES}"""


NEWS_INSTRUCTIONS = f"""You are the NEWS specialist for VISION.

Your domain: market news and narratives.

Tools:
- get_market_headlines — broad market RSS (Reuters/FT/MarketWatch/CNBC/Yahoo)
- search_news — targeted GDELT search for a ticker, theme, or event
- fetch_url — read the full text of a specific article

Approach:
- For "why is X moving": search_news(X) first, then fetch_url the top 1-2 articles for context.
- Cite each source with its URL in `citations`.
- If GDELT returns 0 articles, push an error into `errors` instead of fabricating coverage.
{_SHARED_OUTPUT_RULES}"""


def build_sector_agent() -> Agent:
    return Agent(
        name="sector_specialist",
        instructions=SECTOR_INSTRUCTIONS,
        model=MODEL,
        tools=[get_sector_performance],
        output_type=_RESPONSE_SCHEMA,
    )


def build_stock_agent() -> Agent:
    return Agent(
        name="stock_specialist",
        instructions=STOCK_INSTRUCTIONS,
        model=MODEL,
        tools=[
            get_quote,
            get_price_history,
            get_fundamentals,
            get_earnings,
            compute_indicators,
        ],
        output_type=_RESPONSE_SCHEMA,
    )


def build_screener_agent() -> Agent:
    return Agent(
        name="screener_specialist",
        instructions=SCREENER_INSTRUCTIONS,
        model=MODEL,
        tools=[screen_stocks, screen_universe],
        output_type=_RESPONSE_SCHEMA,
    )


def build_news_agent() -> Agent:
    return Agent(
        name="news_specialist",
        instructions=NEWS_INSTRUCTIONS,
        model=MODEL,
        tools=[get_market_headlines, search_news, fetch_url],
        output_type=_RESPONSE_SCHEMA,
    )
