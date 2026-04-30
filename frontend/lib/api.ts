export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export type ChatStreamEvent =
  | { event: "session"; data: { session_id: string } }
  | { event: "token"; data: { delta: string } }
  | { event: "tool_call"; data: { id: string; name: string; args: Record<string, unknown> } }
  | { event: "tool_error"; data: { id: string; error: string } }
  | { event: "tool_done"; data: { id: string } }
  | { event: "done"; data: { session_id: string; output: string } }
  | { event: "error"; data: { error: string } };

/** Stream chat tokens from the backend over SSE. POST-based, so we can't use
 * the browser's EventSource — we read the body as a stream and parse manually. */
export async function* streamChat(
  message: string,
  sessionId: string | null,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent> {
  const res = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`stream failed: ${res.status} ${await res.text().catch(() => "")}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  // SSE frame separator: spec allows \r\n\r\n (sse-starlette uses this) or \n\n.
  // Find whichever comes first.
  function nextFrameBoundary(buf: string): { idx: number; len: number } | null {
    const a = buf.indexOf("\r\n\r\n");
    const b = buf.indexOf("\n\n");
    if (a === -1 && b === -1) return null;
    if (a === -1) return { idx: b, len: 2 };
    if (b === -1) return { idx: a, len: 4 };
    return a < b ? { idx: a, len: 4 } : { idx: b, len: 2 };
  }

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary;
    while ((boundary = nextFrameBoundary(buffer)) !== null) {
      const frame = buffer.slice(0, boundary.idx);
      buffer = buffer.slice(boundary.idx + boundary.len);

      let eventName = "message";
      const dataLines: string[] = [];
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length === 0) continue;
      try {
        const data = JSON.parse(dataLines.join("\n"));
        yield { event: eventName, data } as ChatStreamEvent;
      } catch {
        // skip malformed
      }
    }
  }
}

export type ScreenFilters = {
  universe?: string;
  tickers?: string[];
  sector?: string | null;
  market_cap_min?: number | null;
  market_cap_max?: number | null;
  pe_min?: number | null;
  pe_max?: number | null;
  rsi_min?: number | null;
  rsi_max?: number | null;
  above_sma_50?: boolean | null;
  above_sma_200?: boolean | null;
  sort_by?: string;
  limit?: number;
  skip_technicals?: boolean;
};

export type ScreenResult = {
  universe: string;
  n_screened: number;
  n_matches: number;
  n_returned: number;
  filters: Record<string, unknown>;
  sort_by: string;
  notices?: string[];
  matches: Array<{
    ticker: string;
    name: string | null;
    sector: string | null;
    industry: string | null;
    market_cap: number | null;
    pe: number | null;
    pb: number | null;
    peg_1y: number | null;
    price: number | null;
    rsi_14?: number | null;
    above_sma_50?: boolean | null;
    above_sma_200?: boolean | null;
    fundamentals_status?: string | null;
    fundamentals_message?: string | null;
  }>;
};

export async function runScreen(filters: ScreenFilters): Promise<ScreenResult> {
  const res = await fetch(`${API_BASE}/api/screen`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(filters),
  });
  if (!res.ok) throw new Error(`screen failed: ${res.status}`);
  return res.json();
}

export type HeatmapItem = {
  ticker: string;
  label?: string;
  name?: string;
  sector?: string;
  value: number;
  price: number | null;
  ret_1d: number | null;
  ret_1w: number | null;
  ret_1m: number | null;
};

export type Heatmap = {
  kind: "sector" | "sp500";
  as_of: string;
  top_n?: number;
  items: HeatmapItem[];
};

export async function getHeatmap(kind: "sector" | "sp500", topN = 100): Promise<Heatmap> {
  const url =
    kind === "sector"
      ? `${API_BASE}/api/heatmap/sector`
      : `${API_BASE}/api/heatmap/sp500?top_n=${topN}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`heatmap failed: ${res.status}`);
  return res.json();
}
