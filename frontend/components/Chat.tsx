"use client";
import { useEffect, useRef, useState } from "react";
import { deleteSession, getSession, streamChat, type HistoryItem } from "@/lib/api";
import Chart from "@/components/Chart";

const SESSION_KEY = "vision_session_id";

/** Split assistant message text on [chart:TICKER] markers and render an
 * inline Chart component between segments. */
const CHART_MARKER = /\[chart:([A-Z][A-Z0-9.-]{0,9})\]/g;

function renderMessageBody(content: string): React.ReactNode[] {
  if (!content) return [];
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  for (const m of content.matchAll(CHART_MARKER)) {
    const idx = m.index ?? 0;
    if (idx > cursor) {
      parts.push(
        <div key={`t${key++}`} className="whitespace-pre-wrap">
          {content.slice(cursor, idx)}
        </div>,
      );
    }
    parts.push(
      <div key={`c${key++}`} className="my-3 -mx-1">
        <Chart ticker={m[1]} compact height={320} initialIndicators={["sma", "rsi"]} />
      </div>,
    );
    cursor = idx + m[0].length;
  }
  if (cursor < content.length) {
    parts.push(
      <div key={`t${key++}`} className="whitespace-pre-wrap">
        {content.slice(cursor)}
      </div>,
    );
  }
  return parts;
}

type ToolStatus = "running" | "done" | "error";
type ToolEvent = {
  id: string;
  name: string;
  args: Record<string, unknown>;
  status: ToolStatus;
  error?: string;
};

type Msg = {
  role: "user" | "assistant";
  content: string;
  tools: ToolEvent[];
  thinking: boolean;  // true while busy and no tokens have arrived yet
};

const SPECIALIST_LABELS: Record<string, { label: string; emoji: string }> = {
  ask_sector_specialist: { label: "Sector specialist", emoji: "📊" },
  ask_stock_specialist: { label: "Stock specialist", emoji: "📈" },
  ask_screener_specialist: { label: "Screener specialist", emoji: "🔎" },
  ask_news_specialist: { label: "News specialist", emoji: "📰" },
};

const TOOL_LABELS: Record<string, string> = {
  get_sector_performance: "Sector performance",
  get_sector_holdings: "Sector holdings",
  get_quote: "Quote",
  get_price_history: "Price history",
  get_fundamentals: "Fundamentals",
  get_earnings: "Earnings",
  compute_indicators: "Technical indicators",
  screen_stocks: "Stock screen",
  screen_universe: "Universe screen",
  get_market_headlines: "Market headlines",
  search_news: "News search",
  fetch_url: "Fetch article",
};

function summarizeArgs(name: string, args: Record<string, unknown>): string {
  // Pull the most informative argument for a one-line subtitle.
  const v = (k: string) => (args[k] != null ? String(args[k]) : "");
  if (name === "get_quote" || name === "get_price_history" || name === "compute_indicators"
      || name === "get_fundamentals" || name === "get_earnings") {
    return v("ticker");
  }
  if (name === "get_sector_holdings") return v("sector_etf");
  if (name === "get_sector_performance") return `${v("lookback_days") || "90"}d lookback`;
  if (name === "search_news") return `"${v("query")}"`;
  if (name === "get_market_headlines") return v("feed") || "all feeds";
  if (name === "fetch_url") {
    const url = v("url");
    try { return new URL(url).hostname; } catch { return url.slice(0, 40); }
  }
  if (name === "screen_stocks" || name === "screen_universe") {
    const parts: string[] = [];
    if (args.universe) parts.push(String(args.universe));
    if (args.sector) parts.push(String(args.sector));
    if (args.pe_max) parts.push(`PE<${args.pe_max}`);
    if (args.rsi_max) parts.push(`RSI<${args.rsi_max}`);
    return parts.join(" · ") || "filters";
  }
  // Specialists pass an `input` string
  if (name.startsWith("ask_")) {
    const input = v("input");
    return input.length > 80 ? input.slice(0, 77) + "…" : input;
  }
  // Fallback — first string-looking arg
  for (const [k, val] of Object.entries(args)) {
    if (typeof val === "string" && val.length < 60) return `${k}: ${val}`;
  }
  return "";
}

function ToolChip({ tool }: { tool: ToolEvent }) {
  const isSpecialist = tool.name.startsWith("ask_");
  const meta = SPECIALIST_LABELS[tool.name];
  const label = meta?.label || TOOL_LABELS[tool.name] || tool.name;
  const subtitle = summarizeArgs(tool.name, tool.args);

  const running = tool.status === "running";
  const errored = tool.status === "error";

  let chipClasses = "bg-panel border-border";
  if (errored) chipClasses = "bg-down/10 border-down/40";
  else if (isSpecialist) chipClasses = "bg-accent/10 border-accent/30";

  return (
    <div
      className={`flex items-start gap-2 text-xs px-2.5 py-1.5 rounded border ${chipClasses} ${
        running ? "" : errored ? "" : "opacity-70"
      }`}
    >
      <span className="mt-0.5">
        {running ? (
          <span className="inline-block w-3 h-3 rounded-full border-2 border-accent border-t-transparent animate-spin" />
        ) : errored ? (
          <span className="inline-block text-down font-mono">✗</span>
        ) : (
          <span className="inline-block text-up font-mono">✓</span>
        )}
      </span>
      <div className="flex-1 min-w-0">
        <div className="font-medium">
          {meta?.emoji && <span className="mr-1">{meta.emoji}</span>}
          {label}
          {errored && <span className="ml-2 text-down text-[10px] uppercase font-mono">error</span>}
        </div>
        {subtitle && (
          <div className="text-muted font-mono text-[11px] truncate">{subtitle}</div>
        )}
        {errored && tool.error && (
          <div className="text-down/90 text-[11px] mt-1 font-mono whitespace-pre-wrap break-words">
            {tool.error}
          </div>
        )}
      </div>
    </div>
  );
}

function ThinkingIndicator() {
  return (
    <div className="flex items-center gap-2 text-muted text-xs">
      <span className="flex gap-1">
        <span className="w-1.5 h-1.5 rounded-full bg-muted animate-bounce" style={{ animationDelay: "0ms" }} />
        <span className="w-1.5 h-1.5 rounded-full bg-muted animate-bounce" style={{ animationDelay: "150ms" }} />
        <span className="w-1.5 h-1.5 rounded-full bg-muted animate-bounce" style={{ animationDelay: "300ms" }} />
      </span>
      <span>Thinking — routing to specialists…</span>
    </div>
  );
}

function HeroEmptyState({ onPick }: { onPick: (q: string) => void }) {
  const examples = [
    "How are sectors performing this week? Highlight any rotation.",
    "Deep dive on NVDA — fundamentals, technicals, recent news.",
    "Find S&P 500 tech names with P/E < 25 and RSI < 50.",
    "What's driving the energy sector right now?",
  ];
  return (
    <div className="flex flex-col items-center justify-center text-center py-12 select-none">
      <div className="text-6xl md:text-7xl font-bold tracking-tight bg-gradient-to-b from-white to-muted bg-clip-text text-transparent mb-3">
        VISION
      </div>
      <div className="text-sm md:text-base text-muted max-w-xl mb-1">
        <span className="text-accent">V</span>erified{" "}
        <span className="text-accent">I</span>ntelligence on{" "}
        <span className="text-accent">S</span>ectors,{" "}
        <span className="text-accent">I</span>nstruments,{" "}
        <span className="text-accent">O</span>pportunities &{" "}
        <span className="text-accent">N</span>arratives
      </div>
      <div className="text-xs text-muted/70 mb-8">
        Multi-agent finance research · gpt-5 · EOD data
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 w-full max-w-2xl">
        {examples.map((q) => (
          <button
            key={q}
            onClick={() => onPick(q)}
            className="text-left text-sm bg-panel hover:bg-panel/70 border border-border hover:border-accent/40 rounded-lg px-3 py-2.5 transition-colors"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Parse the openai-agents `to_input_list()` history into the simple
 * user/assistant messages our UI renders. We only show user turns and the
 * final assistant text — internal reasoning and tool calls are dropped (they
 * already happened; the chip stream for them is gone). */
function parseHistory(history: HistoryItem[]): Msg[] {
  const out: Msg[] = [];
  for (const item of history || []) {
    // User turn — content is a plain string in our flow
    if (item.role === "user" && typeof item.content === "string") {
      out.push({ role: "user", content: item.content, tools: [], thinking: false });
      continue;
    }
    // Assistant message turn — content is a list of output blocks
    if (item.type === "message" && item.role === "assistant" && Array.isArray(item.content)) {
      const text = item.content
        .map((c) => {
          const obj = c as { type?: string; text?: string };
          return obj.type === "output_text" && typeof obj.text === "string" ? obj.text : "";
        })
        .join("");
      if (text) {
        out.push({ role: "assistant", content: text, tools: [], thinking: false });
      }
    }
    // Skip reasoning / function_call / function_call_output items
  }
  return out;
}

export default function Chat() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Restore session on mount — survives navigation between /chat /screener /heatmap
  useEffect(() => {
    if (typeof window === "undefined") return;
    const saved = localStorage.getItem(SESSION_KEY);
    if (!saved) return;
    setRestoring(true);
    getSession(saved)
      .then((s) => {
        setSessionId(s.id);
        setMessages(parseHistory(s.history));
      })
      .catch(() => {
        // Session expired or backend reset — clear stale ID
        localStorage.removeItem(SESSION_KEY);
      })
      .finally(() => setRestoring(false));
  }, []);

  // Persist sessionId changes
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (sessionId) localStorage.setItem(SESSION_KEY, sessionId);
  }, [sessionId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  async function newChat() {
    if (busy) return;
    if (sessionId) {
      // Best-effort delete — don't block on it
      deleteSession(sessionId).catch(() => {});
    }
    setMessages([]);
    setSessionId(null);
    if (typeof window !== "undefined") localStorage.removeItem(SESSION_KEY);
  }

  function stop() {
    abortRef.current?.abort();
  }

  async function send(text?: string) {
    const t = (text ?? input).trim();
    if (!t || busy) return;
    setInput("");
    setBusy(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setMessages((m) => [
      ...m,
      { role: "user", content: t, tools: [], thinking: false },
      { role: "assistant", content: "", tools: [], thinking: true },
    ]);

    const updateLast = (fn: (m: Msg) => Msg) => {
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = fn(copy[copy.length - 1]);
        return copy;
      });
    };

    try {
      for await (const ev of streamChat(t, sessionId, ctrl.signal)) {
        if (ev.event === "session") {
          setSessionId(ev.data.session_id);
        } else if (ev.event === "token") {
          updateLast((last) => ({
            ...last,
            content: last.content + ev.data.delta,
            thinking: false,
          }));
        } else if (ev.event === "tool_call") {
          updateLast((last) => ({
            ...last,
            tools: [
              ...last.tools,
              { id: ev.data.id, name: ev.data.name, args: ev.data.args, status: "running" },
            ],
          }));
        } else if (ev.event === "tool_done") {
          updateLast((last) => ({
            ...last,
            tools: last.tools.map((t, i, arr) => {
              // Don't downgrade an errored tool back to "done"
              if (t.status === "error") return t;
              if (ev.data.id && t.id === ev.data.id) return { ...t, status: "done" };
              if (!ev.data.id) {
                const lastRunningIdx = [...arr].reverse().findIndex((x) => x.status === "running");
                if (lastRunningIdx >= 0 && i === arr.length - 1 - lastRunningIdx) {
                  return { ...t, status: "done" };
                }
              }
              return t;
            }),
          }));
        } else if (ev.event === "tool_error") {
          updateLast((last) => ({
            ...last,
            tools: last.tools.map((t) =>
              t.id === ev.data.id ? { ...t, status: "error", error: ev.data.error } : t,
            ),
          }));
        } else if (ev.event === "error") {
          updateLast((last) => ({
            ...last,
            content: `[error] ${ev.data.error}`,
            thinking: false,
          }));
        }
      }
      // On stream end, finalize any still-running tools (preserve errored)
      updateLast((last) => ({
        ...last,
        thinking: false,
        tools: last.tools.map((t) => (t.status === "running" ? { ...t, status: "done" } : t)),
      }));
    } catch (e) {
      const err = e as { name?: string; message?: string };
      const aborted = err?.name === "AbortError" || ctrl.signal.aborted;
      updateLast((last) => ({
        ...last,
        content: aborted
          ? (last.content || "(stopped)") + (last.content ? "\n\n*(stopped by user)*" : "")
          : `[error] ${err?.message || String(e)}`,
        thinking: false,
        tools: last.tools.map((t) => (t.status === "running" ? { ...t, status: "done" } : t)),
      }));
    } finally {
      abortRef.current = null;
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-130px)]">
      <div ref={scrollRef} className="flex-1 overflow-y-auto pr-2 space-y-4">
        {restoring ? (
          <div className="text-muted text-sm flex items-center gap-2 py-8 justify-center">
            <span className="inline-block w-3 h-3 rounded-full border-2 border-accent border-t-transparent animate-spin" />
            Restoring session…
          </div>
        ) : messages.length === 0 ? (
          <HeroEmptyState onPick={(q) => send(q)} />
        ) : (
          messages.map((m, i) => (
            <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
              <div
                className={
                  m.role === "user"
                    ? "max-w-[85%] bg-accent/15 border border-accent/30 rounded-lg px-3 py-2 text-sm"
                    : "max-w-[90%] bg-panel border border-border rounded-lg px-4 py-3 text-sm prose-chat"
                }
              >
                {m.role === "assistant" && m.tools.length > 0 && (
                  <div className="space-y-1 mb-3">
                    {m.tools.map((t) => (
                      <ToolChip key={t.id || `${t.name}-${Math.random()}`} tool={t} />
                    ))}
                  </div>
                )}
                {m.role === "assistant" && m.thinking && m.content === "" && (
                  <ThinkingIndicator />
                )}
                {m.content && (
                  m.role === "assistant"
                    ? <div>{renderMessageBody(m.content)}</div>
                    : <div className="whitespace-pre-wrap">{m.content}</div>
                )}
              </div>
            </div>
          ))
        )}
      </div>
      <div className="mt-4 flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="Ask VISION anything about sectors, stocks, or markets…"
          disabled={busy}
          className="flex-1 bg-panel border border-border rounded-lg px-4 py-2.5 outline-none focus:border-accent text-sm"
        />
        {busy ? (
          <button
            onClick={stop}
            className="px-4 py-2.5 bg-down text-white font-semibold rounded-lg text-sm hover:bg-down/90 transition-colors"
            title="Stop the current run"
          >
            ◼ Stop
          </button>
        ) : (
          <button
            onClick={() => send()}
            disabled={!input.trim()}
            className="px-4 py-2.5 bg-accent text-bg font-semibold rounded-lg disabled:opacity-50 text-sm"
          >
            Send
          </button>
        )}
      </div>
      <div className="mt-2 flex items-center justify-between text-[11px] text-muted">
        <span className="font-mono">{sessionId ? `session: ${sessionId}` : "no active session"}</span>
        {(sessionId || messages.length > 0) && (
          <button
            onClick={newChat}
            disabled={busy}
            className="hover:text-accent transition-colors disabled:opacity-50"
          >
            New chat ↻
          </button>
        )}
      </div>
    </div>
  );
}
