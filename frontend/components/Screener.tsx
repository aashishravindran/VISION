"use client";
import { useState } from "react";
import { runScreen, type ScreenFilters, type ScreenResult } from "@/lib/api";

const SECTORS = [
  "", // any
  "Information Technology",
  "Financials",
  "Health Care",
  "Energy",
  "Industrials",
  "Consumer Discretionary",
  "Consumer Staples",
  "Utilities",
  "Materials",
  "Real Estate",
  "Communication Services",
];

function fmtMcap(v: number | null | undefined) {
  if (!v) return "—";
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v}`;
}
function fmtNum(v: number | null | undefined, digits = 2) {
  if (v == null) return "—";
  return v.toFixed(digits);
}

export default function Screener() {
  const [filters, setFilters] = useState<ScreenFilters>({
    universe: "sp500",
    sort_by: "market_cap",
    limit: 50,
    skip_technicals: false,
  });
  const [result, setResult] = useState<ScreenResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function set<K extends keyof ScreenFilters>(k: K, v: ScreenFilters[K]) {
    setFilters((f) => ({ ...f, [k]: v }));
  }

  async function run() {
    setBusy(true);
    setErr(null);
    try {
      const r = await runScreen(filters);
      setResult(r);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Stock screener</h1>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 bg-panel border border-border rounded-lg p-4">
        <Field label="Universe">
          <select
            value={filters.universe}
            onChange={(e) => set("universe", e.target.value)}
            className="input"
          >
            <option value="sp500">S&P 500</option>
            <option value="nasdaq100">Nasdaq 100</option>
            <option value="dow30">Dow 30</option>
          </select>
        </Field>
        <Field label="Sector">
          <select
            value={filters.sector ?? ""}
            onChange={(e) => set("sector", e.target.value || null)}
            className="input"
          >
            {SECTORS.map((s) => (
              <option key={s} value={s}>{s || "Any"}</option>
            ))}
          </select>
        </Field>
        <Field label="Market cap min ($)">
          <input
            type="number"
            placeholder="e.g. 10000000000"
            className="input"
            onChange={(e) => set("market_cap_min", e.target.value ? Number(e.target.value) : null)}
          />
        </Field>
        <Field label="Market cap max ($)">
          <input
            type="number"
            placeholder=""
            className="input"
            onChange={(e) => set("market_cap_max", e.target.value ? Number(e.target.value) : null)}
          />
        </Field>
        <Field label="P/E min">
          <input type="number" className="input" step="0.1"
            onChange={(e) => set("pe_min", e.target.value ? Number(e.target.value) : null)} />
        </Field>
        <Field label="P/E max">
          <input type="number" className="input" step="0.1"
            onChange={(e) => set("pe_max", e.target.value ? Number(e.target.value) : null)} />
        </Field>
        <Field label="Sort by">
          <select className="input" value={filters.sort_by}
            onChange={(e) => set("sort_by", e.target.value)}>
            <option value="market_cap">Market cap</option>
            <option value="pe">P/E</option>
            <option value="rsi">RSI</option>
          </select>
        </Field>
        <Field label="RSI min">
          <input type="number" className="input" step="1"
            onChange={(e) => set("rsi_min", e.target.value ? Number(e.target.value) : null)} />
        </Field>
        <Field label="RSI max">
          <input type="number" className="input" step="1" placeholder="e.g. 30 for oversold"
            onChange={(e) => set("rsi_max", e.target.value ? Number(e.target.value) : null)} />
        </Field>
        <Field label="Above SMA 50">
          <select className="input"
            onChange={(e) => set("above_sma_50", e.target.value === "" ? null : e.target.value === "true")}>
            <option value="">Any</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </Field>
        <Field label="Above SMA 200">
          <select className="input"
            onChange={(e) => set("above_sma_200", e.target.value === "" ? null : e.target.value === "true")}>
            <option value="">Any</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </Field>
      </div>

      <div className="flex items-center gap-3">
        <label className="text-sm text-muted">
          <input type="checkbox" checked={!!filters.skip_technicals}
            onChange={(e) => set("skip_technicals", e.target.checked)} className="mr-2" />
          Skip technicals (faster — fundamentals only)
        </label>
        <button onClick={run} disabled={busy}
          className="ml-auto px-4 py-2 bg-accent text-bg font-semibold rounded disabled:opacity-50">
          {busy ? "Screening…" : "Run screen"}
        </button>
      </div>

      {err && <div className="text-down text-sm">Error: {err}</div>}
      {busy && <div className="text-muted text-sm">First run on a universe is slow (~1-2 min) while the cache fills. Re-runs are fast.</div>}

      {result && (
        <div>
          <div className="text-sm text-muted mb-2">
            Universe: <span className="text-foreground font-mono">{result.universe}</span>
            {" · "}Screened {result.n_screened}
            {" · "}Matches {result.n_matches}
            {" · "}Showing {result.n_returned}
          </div>
          {result.notices && result.notices.length > 0 && (
            <div className="mb-3 space-y-1">
              {result.notices.map((n, i) => (
                <div
                  key={i}
                  className="text-xs px-3 py-2 bg-down/10 border border-down/30 rounded text-down/90"
                >
                  ⚠ {n}
                </div>
              ))}
            </div>
          )}
          <div className="overflow-x-auto border border-border rounded-lg">
            <table className="w-full text-sm">
              <thead className="bg-panel text-muted text-xs uppercase">
                <tr>
                  <Th>Ticker</Th>
                  <Th>Name</Th>
                  <Th>Sector</Th>
                  <Th>Price</Th>
                  <Th>Market cap</Th>
                  <Th>P/E</Th>
                  <Th>P/B</Th>
                  <Th>RSI</Th>
                  <Th>&gt;50d</Th>
                  <Th>&gt;200d</Th>
                </tr>
              </thead>
              <tbody>
                {result.matches.map((m) => (
                  <tr key={m.ticker} className="border-t border-border">
                    <Td className="font-mono font-semibold">{m.ticker}</Td>
                    <Td className="max-w-[200px] truncate">{m.name ?? "—"}</Td>
                    <Td className="text-muted text-xs">{m.sector ?? "—"}</Td>
                    <Td>${fmtNum(m.price)}</Td>
                    <Td>{fmtMcap(m.market_cap)}</Td>
                    <Td>{fmtNum(m.pe, 1)}</Td>
                    <Td>{fmtNum(m.pb, 2)}</Td>
                    <Td>{fmtNum(m.rsi_14, 1)}</Td>
                    <Td>{m.above_sma_50 == null ? "—" : m.above_sma_50 ? "✓" : "✗"}</Td>
                    <Td>{m.above_sma_200 == null ? "—" : m.above_sma_200 ? "✓" : "✗"}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <style jsx>{`
        .input {
          width: 100%;
          background: #0b0d12;
          border: 1px solid #222632;
          border-radius: 6px;
          padding: 6px 10px;
          color: #e6e9f0;
          font-size: 0.875rem;
        }
        .input:focus { outline: none; border-color: #7aa2f7; }
      `}</style>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs text-muted mb-1">{label}</span>
      {children}
    </label>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="text-left px-3 py-2 font-medium">{children}</th>;
}
function Td({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-3 py-2 ${className}`}>{children}</td>;
}
