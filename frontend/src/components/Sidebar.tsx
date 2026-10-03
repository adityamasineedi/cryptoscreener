import { NavLink } from "react-router-dom";
import { useOhlcvExpandStore } from "../store/ohlcvExpandStore";

const items = [
  { to: "/", label: "Screener", short: "Sc" },
  { to: "/health", label: "Data Health", short: "DH" },
  { to: "/bos-research", label: "BOS Research", short: "BR" },
  { to: "/bos-strategy-research", label: "BOS Strategies", short: "BS" },
  { to: "/paper", label: "Paper Trade", short: "Pt" },
  { to: "/watchlist", label: "Watchlist", short: "Wl" },
  { to: "/alerts", label: "Alerts", short: "Al" },
  { to: "/charts", label: "Charts", short: "Ch" },
  { to: "/backtest", label: "Backtest", short: "Bt" },
  { to: "/ohlcv-history", label: "OHLCV History", short: "OH" },
  { to: "/settings", label: "Settings", short: "St" },
];

export function Sidebar({
  collapsed,
  onToggle,
}: {
  collapsed: boolean;
  onToggle: () => void;
}) {
  const expandActive = useOhlcvExpandStore((s) => s.active);

  return (
    <aside
      className={`flex h-full min-h-0 w-full shrink-0 flex-col overflow-hidden border-r border-terminal-border bg-terminal-panel/80 backdrop-blur transition-[width] duration-200 ${
        collapsed ? "items-stretch" : ""
      }`}
    >
      <div
        className={`shrink-0 border-b border-terminal-border ${
          collapsed ? "px-1.5 py-2" : "px-4 py-5"
        }`}
      >
        {collapsed ? (
          <div className="flex flex-col items-center gap-2">
            <div
              className="font-display text-sm font-bold tracking-tight text-terminal-text"
              title="Crypto Screener"
            >
              CS
            </div>
            <button
              type="button"
              onClick={onToggle}
              aria-label="Expand sidebar"
              title="Expand"
              className="flex h-7 w-7 items-center justify-center rounded border border-terminal-border text-terminal-muted transition hover:bg-white/5 hover:text-terminal-text"
            >
              <span className="text-xs leading-none" aria-hidden>
                »
              </span>
            </button>
          </div>
        ) : (
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <div className="font-display text-lg font-bold tracking-tight text-terminal-text">
                Crypto Screener
              </div>
              <div className="mt-1 text-[11px] uppercase tracking-[0.18em] text-terminal-muted">
                Live Market Terminal
              </div>
            </div>
            <button
              type="button"
              onClick={onToggle}
              aria-label="Collapse sidebar"
              title="Collapse"
              className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded border border-terminal-border text-terminal-muted transition hover:bg-white/5 hover:text-terminal-text"
            >
              <span className="text-xs leading-none" aria-hidden>
                «
              </span>
            </button>
          </div>
        )}
      </div>
      <nav
        className={`flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto ${
          collapsed ? "p-2" : "p-3"
        }`}
      >
        {items.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            title={item.label}
            className={({ isActive }) =>
              `rounded-md text-sm transition ${
                collapsed
                  ? "flex h-9 items-center justify-center px-1 font-medium"
                  : "px-3 py-2"
              } ${
                isActive
                  ? "bg-terminal-accent/15 text-terminal-accent"
                  : "text-terminal-muted hover:bg-white/5 hover:text-terminal-text"
              }`
            }
          >
            {collapsed ? (
              item.short
            ) : (
              <span className="flex items-center justify-between gap-2">
                <span>{item.label}</span>
                {item.to === "/ohlcv-history" && expandActive ? (
                  <span
                    className="h-1.5 w-1.5 shrink-0 rounded-full bg-terminal-accent"
                    title="Fetch running in background"
                  />
                ) : null}
              </span>
            )}
          </NavLink>
        ))}
      </nav>
      {!collapsed ? (
        <div className="shrink-0 border-t border-terminal-border p-3 text-[11px] text-terminal-muted">
          Real data only · No fabrications
        </div>
      ) : (
        <div
          className="shrink-0 border-t border-terminal-border px-1 py-2 text-center text-[9px] text-terminal-muted"
          title="Real data only · No fabrications"
        >
          ·
        </div>
      )}
    </aside>
  );
}
