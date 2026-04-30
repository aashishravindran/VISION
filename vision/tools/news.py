from datetime import datetime, timedelta

import feedparser
import httpx
from agents import function_tool

from vision import cache
from vision.config import GDELT_DOC_API, RSS_FEEDS


@function_tool
def search_news(query: str, days: int = 7, max_results: int = 25) -> dict:
    """Search recent news articles via the GDELT 2.0 DOC API.

    GDELT indexes news from thousands of sources globally and is free with no
    API key. Use this for ticker-specific news ("NVDA earnings"), themes
    ("AI capex"), sectors ("oil prices"), or events ("Fed rate cut").

    Args:
        query: Search query. Plain words or quoted phrases work; e.g. '"Nvidia earnings"'.
        days: Look back this many days. Default 7.
        max_results: Cap on articles. Default 25; max 100.
    """
    params = {"query": query, "days": days, "max_results": min(max_results, 100)}
    cached = cache.get("search_news", params, ttl_hours=2)
    if cached is not None:
        return cached

    end = datetime.utcnow()
    start = end - timedelta(days=days)
    api_params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(min(max_results, 100)),
        "startdatetime": start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
        "sort": "DateDesc",
    }

    try:
        resp = httpx.get(GDELT_DOC_API, params=api_params, timeout=20.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"query": query, "error": f"GDELT request failed: {e}", "articles": []}

    articles = []
    for art in data.get("articles", []):
        articles.append({
            "title": art.get("title"),
            "url": art.get("url"),
            "source": art.get("domain"),
            "published": art.get("seendate"),
            "language": art.get("language"),
            "sourcecountry": art.get("sourcecountry"),
        })

    out = {"query": query, "lookback_days": days, "n": len(articles), "articles": articles}
    cache.put("search_news", params, out)
    return out


@function_tool
def get_market_headlines(feed: str = "all", limit: int = 30) -> dict:
    """Get the latest market headlines from major financial news RSS feeds.

    Use to scan the general market narrative — what's making news today
    across business and markets reporting. For specific topics or tickers,
    use search_news instead.

    Args:
        feed: One of "all", "reuters_business", "ft_markets", "marketwatch_top",
            "yahoo_finance", "cnbc_top". Default "all" — pulls from all feeds.
        limit: Max headlines to return. Default 30.
    """
    params = {"feed": feed, "limit": limit}
    cached = cache.get("get_market_headlines", params, ttl_hours=1)
    if cached is not None:
        return cached

    feeds_to_try = (
        list(RSS_FEEDS.values()) if feed == "all" else [RSS_FEEDS.get(feed)]
    )
    if not any(feeds_to_try):
        return {"error": f"unknown feed: {feed}", "available": list(RSS_FEEDS.keys())}

    items = []
    for url in feeds_to_try:
        if not url:
            continue
        try:
            parsed = feedparser.parse(url)
            for entry in parsed.entries:
                items.append({
                    "title": entry.get("title"),
                    "url": entry.get("link"),
                    "source": parsed.feed.get("title"),
                    "published": entry.get("published") or entry.get("updated"),
                    "summary": (entry.get("summary") or "")[:500],
                })
        except Exception as e:
            items.append({"feed_error": str(e), "url": url})

    items = [i for i in items if "title" in i][:limit]
    out = {"feed": feed, "n": len(items), "items": items}
    cache.put("get_market_headlines", params, out)
    return out
