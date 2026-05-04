"use client";
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { getHeatmap, type Heatmap as HeatmapData } from "@/lib/api";

// Plotly is heavy and not SSR-friendly — load on the client only
const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

type Metric = "ret_1d" | "ret_1w" | "ret_1m";

const METRIC_LABEL: Record<Metric, string> = {
  ret_1d: "1-day %",
  ret_1w: "1-week %",
  ret_1m: "1-month %",
};

export default function Heatmap() {
  const [kind, setKind] = useState<"sector" | "sp500">("sector");
  const [topN, setTopN] = useState(100);
  const [metric, setMetric] = useState<Metric>("ret_1d");
  const [data, setData] = useState<HeatmapData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Sector heatmap is cheap (1 batch FMP call). S&P 500 is heavier (1 list
  // call + 1-5 batch quote calls); we still gate it behind an explicit Load
  // click so incidental page navigations don't burn the daily quota.
  useEffect(() => {
    if (kind === "sp500") {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    getHeatmap("sector")
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [kind]);

  function loadSp500() {
    setLoading(true);
    setError(null);
    getHeatmap("sp500", topN)
      .then((d) => setData(d))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }

  const items = data?.items ?? [];
  const tickerLabels = items.map((i) => i.ticker);
  const tickerParents =
    kind === "sp500"
      ? items.map((i) => i.sector || "Other")
      : items.map(() => "");
  const tickerValues = items.map((i) => Math.max(i.value || 0, 1));
  const tickerColors = items.map((i) => (i[metric] ?? 0) as number);
  const tickerText = items.map(
    (i) =>
      `${i.ticker}<br>${(i as { name?: string; label?: string }).name ?? i.label ?? ""}` +
      `<br>${i.price ? `$${i.price}` : ""}` +
      `<br>${METRIC_LABEL[metric]}: ${i[metric] ?? "—"}%`,
  );

  // For sp500 we prepend sector parent nodes so the treemap groups by GICS sector
  const sectorParents =
    kind === "sp500"
      ? Array.from(new Set(items.map((i) => i.sector || "Other")))
      : [];
  const finalLabels = kind === "sp500" ? [...sectorParents, ...tickerLabels] : tickerLabels;
  const finalParents =
    kind === "sp500" ? [...sectorParents.map(() => ""), ...tickerParents] : tickerParents;
  const finalValues =
    kind === "sp500" ? [...sectorParents.map(() => 0), ...tickerValues] : tickerValues;
  const finalColors =
    kind === "sp500" ? [...sectorParents.map(() => 0), ...tickerColors] : tickerColors;
  const finalText = kind === "sp500" ? [...sectorParents, ...tickerText] : tickerText;

  return (
    <div>
      <div className="flex items-center gap-3 mb-4 text-sm">
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as "sector" | "sp500")}
          className="bg-panel border border-border rounded px-3 py-1.5"
        >
          <option value="sector">Sectors (SPDR ETFs)</option>
          <option value="sp500">S&P 500 (top by market cap)</option>
        </select>
        {kind === "sp500" && (
          <>
            <select
              value={topN}
              onChange={(e) => setTopN(Number(e.target.value))}
              className="bg-panel border border-border rounded px-3 py-1.5"
            >
              <option value={50}>Top 50</option>
              <option value={100}>Top 100</option>
              <option value={150}>Top 150</option>
              <option value={200}>Top 200</option>
            </select>
            <button
              onClick={loadSp500}
              disabled={loading}
              className="px-3 py-1.5 bg-accent text-bg font-semibold rounded text-sm disabled:opacity-50"
            >
              {data?.kind === "sp500" ? "Reload" : "Load"}
            </button>
          </>
        )}
        <select
          value={metric}
          onChange={(e) => setMetric(e.target.value as Metric)}
          className="bg-panel border border-border rounded px-3 py-1.5"
        >
          <option value="ret_1d">1-day return</option>
          <option value="ret_1w">1-week return</option>
          <option value="ret_1m">1-month return</option>
        </select>
        {data && <span className="text-muted">As of {data.as_of}</span>}
      </div>

      {error && <div className="text-down text-sm">Error: {error}</div>}
      {loading && <div className="text-muted text-sm">Loading… (S&amp;P 500 first run can take ~60s while market caps cache)</div>}
      {!loading && kind === "sp500" && !data && (
        <div className="text-muted text-sm py-3 px-4 rounded border border-border bg-panel">
          S&amp;P 500 heat map fetches the full constituent list + quotes via FMP (~5 API calls cold).
          Click <strong>Load</strong> to fetch — re-runs within 4h are instant from cache.
        </div>
      )}

      {!loading && data && (
        <Plot
          data={[
            {
              type: "treemap",
              labels: finalLabels,
              parents: finalParents,
              values: finalValues,
              text: finalText as unknown as string[],
              hoverinfo: "text",
              textinfo: "label+text",
              marker: {
                colors: finalColors as unknown as number[],
                colorscale: [
                  [0.0, "#7f1d1d"],
                  [0.4, "#ef4444"],
                  [0.5, "#374151"],
                  [0.6, "#10b981"],
                  [1.0, "#065f46"],
                ],
                cmin: -5,
                cmax: 5,
                cmid: 0,
                showscale: true,
                line: { width: 1, color: "#0b0d12" },
              },
              tiling: { packing: "squarify" },
            } as Partial<Plotly.PlotData>,
          ]}
          layout={{
            paper_bgcolor: "#0b0d12",
            plot_bgcolor: "#0b0d12",
            font: { color: "#e6e9f0", family: "ui-sans-serif" },
            margin: { l: 0, r: 0, t: 10, b: 0 },
            height: 720,
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      )}
    </div>
  );
}
