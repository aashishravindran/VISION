"use client";
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { getChart, type ChartData } from "@/lib/api";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

type IndicatorKey = "sma" | "ema" | "bb" | "rsi" | "macd";

type Props = {
  ticker: string;
  lookbackDays?: number;
  height?: number;
  /** Compact mode = no controls header, smaller spacing — for inline-in-chat usage */
  compact?: boolean;
  initialIndicators?: IndicatorKey[];
};

const DEFAULT_INDICATORS: IndicatorKey[] = ["sma", "rsi", "macd"];

function PlotAny(props: Record<string, unknown>) {
  // react-plotly.js's prop types are noisy; cast at the boundary.
  const P = Plot as unknown as React.ComponentType<Record<string, unknown>>;
  return <P {...props} />;
}

export default function Chart({
  ticker,
  lookbackDays = 365,
  height = 560,
  compact = false,
  initialIndicators = DEFAULT_INDICATORS,
}: Props) {
  const [active, setActive] = useState<Set<IndicatorKey>>(new Set(initialIndicators));
  const [data, setData] = useState<ChartData | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    getChart(ticker, lookbackDays, Array.from(active))
      .then((d) => {
        if (cancelled) return;
        if (d.error) {
          setErr(d.error_message || d.error);
          setData(null);
        } else {
          setData(d);
        }
      })
      .catch((e) => !cancelled && setErr(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [ticker, lookbackDays, active]);

  function toggle(k: IndicatorKey) {
    setActive((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  }

  // Build Plotly traces
  const traces: Record<string, unknown>[] = [];
  let priceDomain: [number, number] = [0.0, 1.0]; // y-axis domain on the price subplot
  const yAxes: Record<string, Record<string, unknown>> = {
    yaxis: { title: { text: "Price" }, gridcolor: "#222632" },
  };
  // Map of subpanel key → which yaxis number it lives on (yaxis2/yaxis3...)
  const subpanelAxis: Record<"rsi" | "macd", string> = { rsi: "y", macd: "y" };

  if (data) {
    const hasRsi = !!(active.has("rsi") && data.subpanels.rsi_14);
    const hasMacd = !!(active.has("macd") && data.subpanels.macd);

    // Layout: stack subpanels from y=0 upward; price gets everything above.
    // Order from bottom-up: MACD (if any) → RSI (if any) → price.
    // This way RSI sits adjacent to price, which is the conventional view.
    const subH = 0.18;
    const gap = 0.04;
    const stack: ("macd" | "rsi")[] = [];
    if (hasMacd) stack.push("macd");
    if (hasRsi) stack.push("rsi");

    // Assign each subpanel a y-axis index starting from 2 (yaxis = price)
    stack.forEach((key, idx) => {
      const start = idx * (subH + gap);
      const end = start + subH;
      const axisNum = idx + 2; // yaxis2, yaxis3, ...
      const axisKey = `yaxis${axisNum}`;
      subpanelAxis[key] = `y${axisNum}`;
      yAxes[axisKey] = {
        title: { text: key === "rsi" ? "RSI(14)" : "MACD" },
        domain: [start, end],
        gridcolor: "#222632",
        ...(key === "rsi" ? { range: [0, 100] } : {}),
      };
    });

    // Price gets everything above the last subpanel + a small gap.
    const priceStart = stack.length > 0 ? stack.length * (subH + gap) : 0.0;
    priceDomain = [priceStart, 1.0];

    // Candlesticks
    traces.push({
      type: "candlestick",
      x: data.dates,
      open: data.open,
      high: data.high,
      low: data.low,
      close: data.close,
      name: data.ticker,
      increasing: { line: { color: "#10b981" } },
      decreasing: { line: { color: "#ef4444" } },
      xaxis: "x",
      yaxis: "y",
    });

    if (active.has("sma")) {
      for (const [key, color] of [
        ["sma_20", "#7aa2f7"],
        ["sma_50", "#f59e0b"],
        ["sma_200", "#a78bfa"],
      ] as const) {
        const ys = data.overlays[key];
        if (!ys) continue;
        traces.push({
          type: "scatter",
          mode: "lines",
          x: data.dates,
          y: ys,
          name: key.toUpperCase().replace("_", " "),
          line: { color, width: 1.5 },
          xaxis: "x",
          yaxis: "y",
        });
      }
    }
    if (active.has("ema") && data.overlays.ema_20) {
      traces.push({
        type: "scatter",
        mode: "lines",
        x: data.dates,
        y: data.overlays.ema_20,
        name: "EMA 20",
        line: { color: "#22d3ee", width: 1.5, dash: "dot" },
        xaxis: "x",
        yaxis: "y",
      });
    }
    if (active.has("bb") && data.overlays.bb_upper) {
      for (const [key, color, dash] of [
        ["bb_upper", "rgba(122,162,247,0.5)", undefined],
        ["bb_middle", "rgba(122,162,247,0.3)", "dot"],
        ["bb_lower", "rgba(122,162,247,0.5)", undefined],
      ] as const) {
        const ys = data.overlays[key];
        if (!ys) continue;
        traces.push({
          type: "scatter",
          mode: "lines",
          x: data.dates,
          y: ys,
          name: key.replace("bb_", "BB ").toUpperCase(),
          line: { color, width: 1, ...(dash ? { dash } : {}) },
          xaxis: "x",
          yaxis: "y",
          showlegend: key === "bb_middle",
        });
      }
    }

    // RSI subpanel traces
    if (hasRsi) {
      const yref = subpanelAxis.rsi;
      traces.push({
        type: "scatter",
        mode: "lines",
        x: data.dates,
        y: data.subpanels.rsi_14,
        name: "RSI 14",
        line: { color: "#a78bfa", width: 1.5 },
        xaxis: "x",
        yaxis: yref,
      });
      // 30/70 reference lines
      traces.push({
        type: "scatter",
        mode: "lines",
        x: [data.dates[0], data.dates[data.dates.length - 1]],
        y: [70, 70],
        showlegend: false,
        line: { color: "#ef4444", width: 1, dash: "dash" },
        xaxis: "x",
        yaxis: yref,
      });
      traces.push({
        type: "scatter",
        mode: "lines",
        x: [data.dates[0], data.dates[data.dates.length - 1]],
        y: [30, 30],
        showlegend: false,
        line: { color: "#10b981", width: 1, dash: "dash" },
        xaxis: "x",
        yaxis: yref,
      });
    }

    // MACD subpanel traces
    if (hasMacd) {
      const yref = subpanelAxis.macd;
      traces.push({
        type: "bar",
        x: data.dates,
        y: data.subpanels.macd_hist,
        name: "MACD hist",
        marker: { color: "rgba(122,162,247,0.5)" },
        xaxis: "x",
        yaxis: yref,
      });
      traces.push({
        type: "scatter",
        mode: "lines",
        x: data.dates,
        y: data.subpanels.macd,
        name: "MACD",
        line: { color: "#7aa2f7", width: 1.5 },
        xaxis: "x",
        yaxis: yref,
      });
      traces.push({
        type: "scatter",
        mode: "lines",
        x: data.dates,
        y: data.subpanels.macd_signal,
        name: "Signal",
        line: { color: "#f59e0b", width: 1.5 },
        xaxis: "x",
        yaxis: yref,
      });
    }

    // Apply price y-axis domain (assigned now that we know stack length)
    yAxes.yaxis = {
      ...yAxes.yaxis,
      domain: priceDomain,
    };
  }

  return (
    <div>
      {!compact && (
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <div className="flex items-baseline gap-3">
            <h2 className="text-lg font-semibold font-mono">{ticker.toUpperCase()}</h2>
            {data?.summary && (
              <>
                <span className="text-sm text-muted">
                  ${data.summary.price.toFixed(2)} • {data.as_of}
                </span>
                {data.summary.rsi_14 != null && (
                  <span className="text-xs text-muted">RSI {data.summary.rsi_14.toFixed(1)}</span>
                )}
              </>
            )}
          </div>
          <div className="flex gap-1 text-xs">
            {(["sma", "ema", "bb", "rsi", "macd"] as IndicatorKey[]).map((k) => (
              <button
                key={k}
                onClick={() => toggle(k)}
                className={`px-2 py-1 rounded border ${
                  active.has(k)
                    ? "bg-accent/15 border-accent/40 text-accent"
                    : "bg-panel border-border text-muted hover:border-accent/30"
                }`}
              >
                {k.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
      )}

      {err && (
        <div className="text-sm py-3 px-4 rounded border border-down/30 bg-down/5 text-down/90">
          ⚠ {err}
        </div>
      )}
      {loading && !data && <div className="text-muted text-sm py-2">Loading…</div>}

      {data && (
        <PlotAny
          data={traces}
          layout={{
            paper_bgcolor: "#0b0d12",
            plot_bgcolor: "#0b0d12",
            font: { color: "#e6e9f0", family: "ui-sans-serif, sans-serif", size: 11 },
            margin: { l: 50, r: 20, t: 10, b: 30 },
            height,
            showlegend: !compact,
            legend: { orientation: "h", y: 1.05, x: 0 },
            xaxis: {
              rangeslider: { visible: false },
              gridcolor: "#222632",
              type: "date",
            },
            ...yAxes,
            hovermode: "x unified",
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      )}
    </div>
  );
}
