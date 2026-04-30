"""VISION FastAPI backend.

Routes:
  POST   /api/chat                  — non-streaming chat (returns final answer + history)
  POST   /api/chat/stream           — SSE streaming chat
  GET    /api/sessions              — list saved chat sessions
  GET    /api/sessions/{id}         — get full conversation
  DELETE /api/sessions/{id}         — delete

  POST   /api/screen                — run the screener directly (no LLM)
  GET    /api/heatmap/sector        — sector ETF heatmap data
  GET    /api/heatmap/sp500         — S&P 500 heatmap data (top N by market cap)

  POST   /api/webhooks/inbound      — register a new inbound webhook
  GET    /api/webhooks/inbound      — list
  DELETE /api/webhooks/inbound/{id}
  POST   /api/webhooks/inbound/trigger/{token}  — external services POST here

  POST   /api/webhooks/outbound     — register an outbound alert subscription
  GET    /api/webhooks/outbound
  DELETE /api/webhooks/outbound/{id}
"""
import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from typing import Any

from agents import Runner
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from openai.types.responses import ResponseTextDeltaEvent
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from vision import store
from vision.agents import build_orchestrator
from vision.heatmap import get_sector_heatmap, get_sp500_heatmap
from vision.tools.screener import _screen_stocks

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    yield


app = FastAPI(title="VISION API", version="0.4.0", lifespan=lifespan)


# Single orchestrator instance — agents are stateless containers, safe to reuse.
ORCHESTRATOR = build_orchestrator()

# CORS — open in dev. Tighten for prod.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Schemas ---

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    output: str
    history: list[dict]


class ScreenRequest(BaseModel):
    universe: str = "sp500"
    tickers: list[str] | None = None
    sector: str | None = None
    market_cap_min: float | None = None
    market_cap_max: float | None = None
    pe_min: float | None = None
    pe_max: float | None = None
    rsi_min: float | None = None
    rsi_max: float | None = None
    above_sma_50: bool | None = None
    above_sma_200: bool | None = None
    sort_by: str = "market_cap"
    limit: int = 50
    skip_technicals: bool = False


class InboundWebhookCreate(BaseModel):
    name: str
    template: str | None = None  # query template; if None, payload must include "query"


class OutboundAlertCreate(BaseModel):
    name: str
    trigger_query: str
    schedule_cron: str | None = None
    target_url: str | None = None
    channel: str | None = None  # slack, discord, email — channel impl deferred


# --- Helpers ---

def _build_input(history: list[dict], new_message: str) -> list[dict]:
    """Append a new user message to existing history for the next agent run."""
    return history + [{"role": "user", "content": new_message}]


_ERROR_HINTS = (
    "error", "failed", "401", "403", "429", "500", "502", "503", "timeout",
    "rate limit", "unauthorized", "forbidden", "not found", "no data",
)


def _extract_tool_error(output: Any) -> str | None:
    """Best-effort: detect when a tool returned an error payload.

    Tools return either a JSON dict like {"error": "..."}, an empty/sparse dict,
    or a plain string. We surface clear errors and silent failures so the user
    sees what broke instead of the agent quietly working around it."""
    if output is None:
        return None
    # Stringified tool output (most common via MCP)
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
        except Exception:
            text = output.lower()
            if any(h in text for h in _ERROR_HINTS) and len(output) < 600:
                return output
            return None
        return _extract_tool_error(parsed)
    if isinstance(output, dict):
        for k in ("error", "errors", "error_message", "message"):
            v = output.get(k)
            if isinstance(v, str) and v:
                low = v.lower()
                if any(h in low for h in _ERROR_HINTS) or k.startswith("error"):
                    return v
        if output.get("is_error") is True:
            return str(output.get("content") or output.get("message") or "tool error")
    return None


# --- Chat ---

@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not set on the server")

    session_id = req.session_id or f"sess_{secrets.token_hex(8)}"
    session = store.get_session(session_id)
    history = session["history"] if session else []

    agent_input = _build_input(history, req.message)
    result = await Runner.run(ORCHESTRATOR, agent_input, max_turns=20)

    new_history = result.to_input_list()
    title = session["title"] if session and session.get("title") else req.message[:60]
    store.upsert_session_history(session_id, new_history, title=title)

    return ChatResponse(
        session_id=session_id,
        output=result.final_output or "",
        history=new_history,
    )


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not set on the server")

    session_id = req.session_id or f"sess_{secrets.token_hex(8)}"
    session = store.get_session(session_id)
    history = session["history"] if session else []
    agent_input = _build_input(history, req.message)

    async def event_generator():
        # Tell the client its session_id immediately
        yield {"event": "session", "data": json.dumps({"session_id": session_id})}

        result = Runner.run_streamed(ORCHESTRATOR, agent_input, max_turns=20)

        try:
            async for event in result.stream_events():
                # Token deltas (the meat of the stream)
                if event.type == "raw_response_event":
                    if isinstance(event.data, ResponseTextDeltaEvent):
                        yield {"event": "token", "data": json.dumps({"delta": event.data.delta})}
                    continue

                # Tool call lifecycle for UI affordances
                if event.type == "run_item_stream_event":
                    item = event.item
                    kind = type(item).__name__
                    if kind == "ToolCallItem":
                        raw = getattr(item, "raw_item", None)
                        name = getattr(raw, "name", "?") if raw else "?"
                        args_raw = getattr(raw, "arguments", "") if raw else ""
                        # arguments is a JSON string from the model — parse for display
                        try:
                            args = json.loads(args_raw) if args_raw else {}
                        except Exception:
                            args = {"_raw": str(args_raw)[:200]}
                        call_id = getattr(raw, "call_id", None) or getattr(raw, "id", None) or ""
                        yield {"event": "tool_call", "data": json.dumps({
                            "id": call_id, "name": name, "args": args,
                        })}
                    elif kind == "ToolCallOutputItem":
                        raw = getattr(item, "raw_item", None) or {}
                        call_id = (
                            raw.get("call_id") if isinstance(raw, dict)
                            else getattr(raw, "call_id", "") or getattr(raw, "id", "")
                        )
                        # Inspect the output for tool-side errors so the UI can
                        # surface them as a separate event (red chip).
                        output_obj = getattr(item, "output", None)
                        err_text = _extract_tool_error(output_obj)
                        if err_text:
                            yield {"event": "tool_error", "data": json.dumps({
                                "id": call_id or "",
                                "error": err_text[:500],
                            })}
                        yield {"event": "tool_done", "data": json.dumps({"id": call_id or ""})}

            # Final state — persist and signal end
            new_history = result.to_input_list()
            title = session["title"] if session and session.get("title") else req.message[:60]
            store.upsert_session_history(session_id, new_history, title=title)

            yield {"event": "done", "data": json.dumps({
                "session_id": session_id,
                "output": result.final_output or "",
            })}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)})}

    return EventSourceResponse(event_generator())


@app.get("/api/sessions")
def list_sessions(limit: int = 50):
    return {"sessions": store.list_sessions(limit)}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    s = store.get_session(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    return s


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    store.delete_session(session_id)
    return {"ok": True}


# --- Screener (direct, no LLM) ---

@app.post("/api/screen")
async def api_screen(req: ScreenRequest):
    """Run the screener directly without going through the LLM. Fast path
    for the screener UI — the agent uses the same code via the FunctionTool
    wrapper, which delegates to the same `_screen_stocks` implementation."""
    return await asyncio.to_thread(_screen_stocks, **req.model_dump())


# --- Heat maps ---

@app.get("/api/heatmap/sector")
def api_heatmap_sector():
    return get_sector_heatmap()


@app.get("/api/heatmap/sp500")
def api_heatmap_sp500(top_n: int = 100):
    top_n = min(max(top_n, 10), 200)
    return get_sp500_heatmap(top_n=top_n)


# --- Webhooks: inbound ---

@app.post("/api/webhooks/inbound")
def create_inbound(req: InboundWebhookCreate):
    return store.create_inbound_webhook(req.name, req.template)


@app.get("/api/webhooks/inbound")
def list_inbound():
    return {"webhooks": store.list_inbound_webhooks()}


@app.delete("/api/webhooks/inbound/{webhook_id}")
def delete_inbound(webhook_id: str):
    store.delete_inbound_webhook(webhook_id)
    return {"ok": True}


@app.post("/api/webhooks/inbound/trigger/{token}")
async def trigger_inbound(token: str, request: Request):
    """External-facing endpoint. Anyone with the token can POST here to run an agent.

    The webhook can be configured with a query template (see InboundWebhookCreate);
    if no template, the payload must include a "query" string.

    Example payload:
        { "query": "Brief on TSLA" }
        or with a template "Brief on {ticker}":
        { "ticker": "TSLA" }
    """
    wh = store.get_inbound_webhook(token)
    if not wh:
        raise HTTPException(404, "unknown webhook token")

    payload: dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    template = wh.get("template")
    if template:
        try:
            query = template.format(**payload)
        except KeyError as e:
            raise HTTPException(400, f"template placeholder missing in payload: {e}")
    else:
        query = payload.get("query")
        if not query:
            raise HTTPException(400, "payload must include 'query' (no template configured)")

    result = await Runner.run(ORCHESTRATOR, query, max_turns=20)
    output = result.final_output or ""
    store.record_inbound_run(wh["id"], payload, output)
    return {"webhook_id": wh["id"], "query": query, "output": output}


# --- Webhooks: outbound (alerts — channel firing deferred) ---

@app.post("/api/webhooks/outbound")
def create_outbound(req: OutboundAlertCreate):
    return store.create_outbound_alert(
        req.name, req.trigger_query, req.schedule_cron, req.target_url, req.channel
    )


@app.get("/api/webhooks/outbound")
def list_outbound():
    return {"alerts": store.list_outbound_alerts()}


@app.delete("/api/webhooks/outbound/{alert_id}")
def delete_outbound(alert_id: str):
    store.delete_outbound_alert(alert_id)
    return {"ok": True}


# --- Health ---

@app.get("/api/health")
def health():
    return {"status": "ok", "agents": ["sector", "stock", "screener", "news"]}
