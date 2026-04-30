# VISION

**V**erified **I**ntelligence on **S**ectors, **I**nstruments, **O**pportunities & **N**arratives.

Multi-agent finance research system. A top-level orchestrator delegates to four specialist sub-agents in parallel — sector, stock, screener, news. FastAPI backend + OpenAI Agents SDK; data from **Tiingo** (prices, market caps, fundamentals) and **GDELT** + RSS (news). Next.js frontend with streaming chat (live tool-call display + inline error chips), screener, and heat map.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Frontend (Next.js 15, React 19, Tailwind, react-plotly)     │
│  - Chat (SSE stream, tool chips, error chips)                │
│  - Screener  - Heat map                                      │
└──────────────────────────┬───────────────────────────────────┘
                           │ HTTP / SSE
┌──────────────────────────▼───────────────────────────────────┐
│  FastAPI backend (vision/api.py)                             │
│  - /api/chat[/stream]    - /api/screen                       │
│  - /api/heatmap/{sector,sp500}                               │
│  - /api/webhooks/{inbound,outbound}                          │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
              ┌─────────────────────────┐
              │  Orchestrator (gpt-5)   │
              └────────────┬────────────┘
                           │  agent.as_tool() — runs in parallel
        ┌──────────┬───────┴───────┬────────────┐
        ▼          ▼               ▼            ▼
   sector_      stock_         screener_     news_
   specialist   specialist     specialist    specialist
        │          │               │            │
        └──────────┴───┬───────────┴────────────┘
                      ▼
       ┌─────────────────────────────────────────┐
       │  Tiingo (prices, market caps,           │
       │           fundamentals, daily metrics)  │
       │  GDELT + RSS (news)                     │
       │  trafilatura (article text)             │
       │  pandas + ta (technical indicators)     │
       └─────────────────────────────────────────┘

                  SQLite cache (WAL)
```

## Project layout

```
~/Documents/VISION/
├── vision/
│   ├── agents/
│   │   ├── orchestrator.py   # delegates to specialists in parallel
│   │   └── specialists.py    # sector / stock / screener / news
│   ├── data/
│   │   └── tiingo.py         # prices + market caps + fundamentals
│   ├── tools/
│   │   ├── prices.py         # get_quote, get_price_history
│   │   ├── stocks.py         # get_fundamentals, get_earnings
│   │   ├── indicators.py     # compute_indicators, screen_universe (ta lib)
│   │   ├── screener.py       # screen_stocks across sp500/ndx/dow
│   │   ├── sectors.py        # get_sector_performance
│   │   ├── news.py           # GDELT + RSS
│   │   ├── web.py            # article fetch (trafilatura)
│   │   └── universes.py      # S&P 500 / NDX / Dow ticker lists (Wikipedia)
│   ├── api.py                # FastAPI app
│   ├── store.py              # SQLite — sessions + webhooks
│   ├── heatmap.py            # sector + S&P 500 heat map
│   ├── cache.py              # SQLite KV cache (WAL)
│   ├── config.py
│   └── agent.py              # CLI helper
├── frontend/                 # Next.js 15 (App Router)
├── run.py                    # CLI
└── requirements.txt, .env.example
```

## Setup

### 1. One-time install

```bash
cd ~/Documents/VISION
python3 -m venv .venv             # if not already
cp .env.example .env              # then fill in OPENAI_API_KEY + TIINGO_API_KEY
brew install node                 # if not already
make install                      # installs Python + frontend deps
```

Get a free Tiingo key at https://www.tiingo.com/account/api/token (500 req/day — plenty with our caching).

### 2. Run

```bash
make dev          # backend (:8000) in background + frontend (:3000) in foreground
                  # Ctrl+C stops both

# Or run them separately in two terminals:
make backend      # FastAPI auto-reload on :8000
make frontend     # Next.js dev on :3000
```

Other handy targets:
```bash
make stop         # kill any lingering uvicorn / next dev
make log          # tail backend log (when started via `make dev`)
make clean        # drop caches (SQLite, .next, __pycache__)
```

Open http://localhost:3000.

### CLI (optional)

```bash
python run.py "Deep dive on NVDA"
python run.py -v "How are sectors performing this week?"   # -v shows tool calls
```

## What's in v4

- **Tiingo as the only data source.** Single-process app, two env vars total (`OPENAI_API_KEY`, `TIINGO_API_KEY`).
- **No external MCP server, no Docker, no separate clones.** Everything in this repo.
- **Multi-agent + parallel delegation.** Orchestrator fans out to specialists in parallel via `.as_tool()`.
- **Inline error surfacing.** Tool errors render as red chips in chat instead of getting swallowed. Specialist prompts also instruct them to never bury data-fetch failures.
- **SQLite-backed cache (WAL).** Aggressive caching: 12h prices, 24h fundamentals, 3d market caps, 4h heat map. The free Tiingo tier handles real usage easily.

## What's no longer here (v3 → v4 changes)

- ~~maverick-mcp~~ — dropped. We weren't using its 39 tools, only ~8 we already had locally.
- ~~Stooq~~ — dropped. Their free CSV API was discontinued.
- ~~yfinance~~ — dropped. Yahoo's anti-bot crumb invalidation made it unreliable for bulk operations.
- **Earnings forward calendar** — Tiingo's free tier doesn't include this; the `get_earnings` tool now surfaces only historical EPS / revenue and explicitly says so when asked about future dates.

## API examples

### Chat (streaming)
```bash
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message":"Deep dive on NVDA — fundamentals, technicals, recent news"}'
```

### Screener
```bash
curl -X POST http://localhost:8000/api/screen \
  -H "Content-Type: application/json" \
  -d '{
    "universe":"sp500",
    "sector":"Information Technology",
    "pe_max":25,
    "rsi_max":50,
    "limit":20
  }'
```

### Heat map
```bash
curl http://localhost:8000/api/heatmap/sector
curl 'http://localhost:8000/api/heatmap/sp500?top_n=100'
```

### Inbound webhook
```bash
curl -X POST http://localhost:8000/api/webhooks/inbound \
  -H "Content-Type: application/json" \
  -d '{"name":"ticker-brief","template":"Brief on {ticker}"}'
# → returns { id, token, ... }

curl -X POST http://localhost:8000/api/webhooks/inbound/trigger/<token> \
  -H "Content-Type: application/json" \
  -d '{"ticker":"TSLA"}'
```

## Notes & limits

- **EOD only.** All prices / indicators are end-of-day (Tiingo).
- **First runs are slow.** Caches warm in 30-60s on first heat map / first screener run; near-instant after.
- **Forward earnings dates are not on the free Tiingo tier.** The agent will tell you when asked.
- **Outbound webhook channel firing not yet wired.** Subscriptions are stored; we'll add the scheduler + HTTP-fire later.
- **No auth on the API.** Local dev only. Add auth before exposing.
