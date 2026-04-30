import httpx
import trafilatura
from agents import function_tool

from vision import cache

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@function_tool
def fetch_url(url: str, max_chars: int = 8000) -> dict:
    """Fetch a web page and extract its main article text.

    Use to read a specific news article, blog post, press release, or any
    web page surfaced by search_news or get_market_headlines. Returns clean
    article text (boilerplate, nav, and ads stripped via trafilatura).

    Args:
        url: Full URL to fetch.
        max_chars: Truncate the extracted text to this length. Default 8000.
    """
    params = {"url": url, "max_chars": max_chars}
    cached = cache.get("fetch_url", params, ttl_hours=24)
    if cached is not None:
        return cached

    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=20.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as e:
        return {"url": url, "error": f"fetch failed: {e}"}

    extracted = trafilatura.extract(
        resp.text,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    )
    if not extracted:
        extracted = resp.text[:max_chars]

    truncated = len(extracted) > max_chars
    out = {
        "url": str(resp.url),
        "status": resp.status_code,
        "content": extracted[:max_chars],
        "truncated": truncated,
        "original_length_chars": len(extracted),
    }
    cache.put("fetch_url", params, out)
    return out
