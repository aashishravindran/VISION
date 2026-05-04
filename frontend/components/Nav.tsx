"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function Nav() {
  const [tickerInput, setTickerInput] = useState("");
  const router = useRouter();

  function go(e: React.FormEvent) {
    e.preventDefault();
    const t = tickerInput.trim().toUpperCase();
    if (t) router.push(`/chart/${t}`);
    setTickerInput("");
  }

  return (
    <header className="border-b border-border bg-panel/40">
      <div className="max-w-7xl mx-auto px-4 py-3 flex items-center gap-6">
        <Link href="/" className="font-semibold tracking-wide">
          VISION
        </Link>
        <nav className="flex gap-4 text-sm text-muted">
          <Link href="/" className="hover:text-accent">Chat</Link>
          <Link href="/screener" className="hover:text-accent">Screener</Link>
          <Link href="/heatmap" className="hover:text-accent">Heat map</Link>
        </nav>
        <form onSubmit={go} className="ml-auto">
          <input
            value={tickerInput}
            onChange={(e) => setTickerInput(e.target.value)}
            placeholder="Chart ticker…"
            className="bg-bg border border-border rounded px-3 py-1 text-xs w-32 focus:border-accent outline-none"
          />
        </form>
      </div>
    </header>
  );
}
