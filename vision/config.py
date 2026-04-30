from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DB = ROOT / "cache" / "vision.duckdb"
CHARTS_DIR = ROOT / "charts"

SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLE": "Energy",
    "XLI": "Industrials",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}

BENCHMARK_ETFS = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "IWM": "Russell 2000",
    "DIA": "Dow Jones",
}

# RSS feeds for general market narrative
RSS_FEEDS = {
    "reuters_business": "https://feeds.reuters.com/reuters/businessNews",
    "ft_markets": "https://www.ft.com/markets?format=rss",
    "marketwatch_top": "https://feeds.marketwatch.com/marketwatch/topstories/",
    "yahoo_finance": "https://finance.yahoo.com/news/rssindex",
    "cnbc_top": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
}

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

# Defaults
DEFAULT_LOOKBACK_DAYS = 365
CACHE_TTL_HOURS = 12  # EOD data is daily, but be conservative for intraday refreshes
