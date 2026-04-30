import Link from "next/link";

export default function Nav() {
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
      </div>
    </header>
  );
}
