"""Web-search fallback for tickers FMP's free tier doesn't cover.

When the agent gets `error: "tier_gated"` from a per-ticker FMP tool (common
for mining, commodities, and many international names), it should call this
tool. We use the OpenAI Responses API's built-in `web_search` so a small
model can browse public sources (Yahoo Finance, Finviz, company IR, etc.)
and return a structured summary.

Costs more than an FMP call (~one web_search invocation per call), so we
cache aggressively (24h) and the tool is gated behind explicit agent decisions.
"""
from __future__ import annotations

import json
import os
from typing import Any

from agents import function_tool
from openai import OpenAI

from vision import cache

WEB_LOOKUP_MODEL = os.environ.get("VISION_WEB_LOOKUP_MODEL", "gpt-5-mini")

LOOKUP_PROMPT = """You are a finance research bot. The user asks about ticker {ticker}.

Use web_search to fetch the latest authoritative data from public sources
(Yahoo Finance, Finviz, the company's IR page, Reuters, Bloomberg). Cite the
source URL for each non-trivial fact.

Return a concise JSON object with these keys (use null for anything you genuinely cannot find):
{{
  "ticker": "...",
  "name": "...",
  "exchange": "...",
  "sector": "...",
  "industry": "...",
  "country": "...",
  "description": "...",          // 2-4 sentence company description
  "last_close_usd": ...,         // latest close in USD if available
  "as_of_date": "YYYY-MM-DD",    // date of the last_close
  "market_cap_usd": ...,         // approximate, in USD
  "pe_ratio": ...,
  "dividend_yield_pct": ...,
  "ytd_return_pct": ...,
  "1m_return_pct": ...,
  "1y_return_pct": ...,
  "recent_news_themes": ["..."], // 2-4 short bullets on what's in the news
  "sources": ["url1", "url2"],   // every URL you cited
  "notes": "..."                 // any caveats or unverified facts
}}

Output ONLY the JSON object — no preamble, no markdown fences."""


def _call_responses_api(ticker: str) -> str:
    """Run the Responses API + web_search and return the raw text output."""
    client = OpenAI()
    response = client.responses.create(
        model=WEB_LOOKUP_MODEL,
        tools=[{"type": "web_search"}],
        input=[{
            "role": "user",
            "content": [{"type": "input_text", "text": LOOKUP_PROMPT.format(ticker=ticker)}],
        }],
    )
    # Extract the final text from the response
    out = ""
    for item in (getattr(response, "output", None) or []):
        if getattr(item, "type", None) != "message":
            continue
        for c in (getattr(item, "content", None) or []):
            if getattr(c, "type", None) == "output_text":
                out += getattr(c, "text", "") or ""
    if not out:
        out = (getattr(response, "output_text", "") or "").strip()
    return out


@function_tool
def lookup_ticker_via_web(ticker: str) -> dict:
    """Look up a ticker via web_search when FMP's free tier doesn't cover it.

    USE THIS WHEN: get_quote / get_fundamentals / compute_indicators returned
    `error: "tier_gated"` — that means FMP's free tier doesn't include this
    ticker (common for mining, commodities, smaller caps, international).

    DO NOT USE WHEN: the ticker worked on FMP. FMP data is structured and
    cheap; this tool is more expensive (web_search + LLM).

    Returns a structured dict with current price, market cap, P/E, recent
    returns, sector/industry, and recent news themes — sourced from
    authoritative public sources with citation URLs.

    Args:
        ticker: Stock symbol, e.g. "NEM", "GFI", "AEM", "VFS".
    """
    cache_key = {"ticker": ticker.upper()}
    cached = cache.get("web_lookup_ticker", cache_key, ttl_hours=24)
    if cached is not None:
        return cached

    try:
        raw = _call_responses_api(ticker.upper())
    except Exception as e:
        return {"ticker": ticker.upper(), "error": "web_lookup_failed", "error_message": str(e)}

    if not raw:
        return {"ticker": ticker.upper(), "error": "web_lookup_empty",
                "error_message": "Web lookup returned no output."}

    # Try to parse the JSON the model emitted. If it's wrapped in fences, strip them.
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # Strip ```json ... ``` or ``` ... ```
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
    try:
        parsed = json.loads(cleaned)
    except Exception:
        # Surface the raw text so the agent can still reason over it
        out = {"ticker": ticker.upper(), "raw_findings": raw[:2000],
               "parse_warning": "Web lookup returned non-JSON; surfacing raw text."}
        cache.put("web_lookup_ticker", cache_key, out)
        return out

    if isinstance(parsed, dict):
        parsed.setdefault("ticker", ticker.upper())
        parsed["_source"] = "web_search"
        cache.put("web_lookup_ticker", cache_key, parsed)
        return parsed

    return {"ticker": ticker.upper(), "raw_findings": raw[:2000]}


NEWS_PROMPT = """You are a finance news researcher. Find recent news (last 30 days) about: {query}

Use web_search to pull articles from authoritative sources (Reuters, Bloomberg,
WSJ, FT, MarketWatch, CNBC, official IR pages). Cite the source URL for every
article you reference.

Return a concise JSON object with these keys (use null/empty list when nothing fits):
{{
  "query": "...",
  "narrative": "...",     // 2-4 sentence summary of what's actually moving the story
  "themes": ["..."],      // 2-5 short bullets on the key themes / drivers
  "articles": [           // 4-8 articles ranked by relevance
    {{
      "title": "...",
      "url": "...",
      "source": "...",
      "published": "YYYY-MM-DD",
      "summary": "..."    // 1-2 sentences on what this article specifically says
    }}
  ],
  "notes": "..."          // any caveats about coverage gaps or unverified facts
}}

Output ONLY the JSON object — no preamble, no markdown fences."""


def _call_news_responses_api(query: str) -> str:
    client = OpenAI()
    response = client.responses.create(
        model=WEB_LOOKUP_MODEL,
        tools=[{"type": "web_search"}],
        input=[{
            "role": "user",
            "content": [{"type": "input_text", "text": NEWS_PROMPT.format(query=query)}],
        }],
    )
    out = ""
    for item in (getattr(response, "output", None) or []):
        if getattr(item, "type", None) != "message":
            continue
        for c in (getattr(item, "content", None) or []):
            if getattr(c, "type", None) == "output_text":
                out += getattr(c, "text", "") or ""
    if not out:
        out = (getattr(response, "output_text", "") or "").strip()
    return out


@function_tool
def lookup_news_via_web(query: str) -> dict:
    """Look up recent news via web_search when GDELT can't fulfill the request.

    USE THIS WHEN: search_news returned `error: "rate_limited"` /
    `"timeout"` / `"network_error"` / `"gdelt_error"` — that means GDELT
    couldn't serve the request. This tool uses GPT-5-mini + web_search to
    pull authoritative articles directly.

    DO NOT USE WHEN: search_news worked — that data is cached and free.
    Only use this as a fallback or when GDELT's coverage is known thin
    (very small companies, breaking events <1h old).

    Returns structured news with citation URLs sourced from Reuters /
    Bloomberg / WSJ / FT / MarketWatch / CNBC / company IR.

    Args:
        query: Search query — ticker name, theme, or event ("KTOS contract
            awards", "AI capex 2026", "Fed rate decision").
    """
    cache_key = {"query": query}
    cached = cache.get("web_lookup_news", cache_key, ttl_hours=6)
    if cached is not None:
        return cached

    try:
        raw = _call_news_responses_api(query)
    except Exception as e:
        return {"query": query, "error": "web_lookup_failed", "error_message": str(e)}

    if not raw:
        return {"query": query, "error": "web_lookup_empty",
                "error_message": "Web news lookup returned no output."}

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
    try:
        parsed = json.loads(cleaned)
    except Exception:
        out = {"query": query, "raw_findings": raw[:2500],
               "parse_warning": "Web news lookup returned non-JSON; surfacing raw text."}
        cache.put("web_lookup_news", cache_key, out)
        return out

    if isinstance(parsed, dict):
        parsed.setdefault("query", query)
        parsed["_source"] = "web_search"
        cache.put("web_lookup_news", cache_key, parsed)
        return parsed

    return {"query": query, "raw_findings": raw[:2500]}
