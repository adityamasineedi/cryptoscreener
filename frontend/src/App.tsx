import { useEffect, useState } from "react";
import { Route, Routes } from "react-router-dom";
import { fetchHealth } from "./api/client";
import { BottomCharts } from "./components/BottomCharts";
import { ChartsPage } from "./components/ChartsPage";
import { CoinDetailPanel } from "./components/CoinDetailPanel";
import { BacktestPanel } from "./components/BacktestPanel";
import { BosResearchPanel } from "./components/BosResearchPanel";
import { BosStrategyResearchPanel } from "./components/BosStrategyResearchPanel";
import { DataHealthPanel } from "./components/DataHealthPanel";
import { AlertsPanel } from "./components/AlertsPanel";
import { BacktestBanner } from "./components/BacktestBanner";
import { OhlcvExpandBanner } from "./components/OhlcvExpandBanner";
import { OhlcvHistoryPanel } from "./components/OhlcvHistoryPanel";
import { PaperTradePanel } from "./components/PaperTradePanel";
import { ScreenerTable } from "./components/ScreenerTable";
import { Sidebar } from "./components/Sidebar";
import { StrategiesPanel } from "./components/StrategiesPanel";
import { TopNav } from "./components/TopNav";
import { DiagnosticsPanel } from "./components/diagnostics/DiagnosticsPanel";
import { useMarketStream } from "./hooks/useMarketStream";
import { useMediaQuery } from "./hooks/useMediaQuery";
import { useMarketStore } from "./store/marketStore";
import { startBacktestBackgroundPoller } from "./store/backtestJobStore";
import { startOhlcvExpandBackgroundPoller } from "./store/ohlcvExpandStore";
import type { HealthResponse } from "./types/market";

function Placeholder({ title }: { title: string }) {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center text-terminal-muted">
      {title} — coming in later phases. No mock data.
    </div>
  );
}

function ScreenerPage() {
  useMarketStream();
  const domain = useMarketStore((s) => s.domain);
  const setDomain = useMarketStore((s) => s.setDomain);
  const connected = useMarketStore((s) => s.connected);
  const ingestion = useMarketStore((s) => s.ingestion);
  const total = useMarketStore((s) => s.total);
  const screenMeta = useMarketStore((s) => s.screenMeta);
  const tickerCount = useMarketStore((s) => Object.keys(s.rows).length);
  const selected = useMarketStore((s) => s.selectedSymbol);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const isNarrow = useMediaQuery("(max-width: 1099px)");
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const h = await fetchHealth();
        if (alive) setHealth(h);
      } catch {
        /* ignore */
      }
    }
    poll();
    const id = window.setInterval(poll, 5000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (isNarrow && selected) setDrawerOpen(true);
    if (!isNarrow) setDrawerOpen(false);
  }, [selected, isNarrow]);

  return (
    <div className="screener-page">
      <TopNav
        active={domain}
        onChange={setDomain}
        health={{
          connected,
          ingestion: health?.ingestion ?? ingestion,
          // Data Health / nav: full backend universe (not screen top-100)
          symbols: health?.symbols_loaded ?? screenMeta?.total_universe ?? total,
          tickers: health?.tickers_live ?? tickerCount,
          screenReturned: screenMeta?.returned_count,
          screenUniverse: screenMeta?.total_universe ?? health?.symbols_loaded,
        }}
        detailToggle={
          isNarrow
            ? {
                open: drawerOpen,
                onToggle: () => setDrawerOpen((v) => !v),
                hasSelection: Boolean(selected),
              }
            : undefined
        }
      />
      <div className="market-area">
        <ScreenerTable domain={domain} />
        {!isNarrow ? (
          <CoinDetailPanel />
        ) : drawerOpen ? (
          <>
            <button
              type="button"
              aria-label="Close detail"
              className="detail-drawer-backdrop"
              onClick={() => setDrawerOpen(false)}
            />
            <div className="detail-drawer">
              <CoinDetailPanel
                onClose={() => setDrawerOpen(false)}
                compactHeader
              />
            </div>
          </>
        ) : null}
      </div>
      <BottomCharts />
    </div>
  );
}

const SIDEBAR_KEY = "cs.sidebar.collapsed";

export default function App() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_KEY, sidebarCollapsed ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [sidebarCollapsed]);

  // Long-running research jobs run on the API; keep polling across route changes.
  useEffect(() => startOhlcvExpandBackgroundPoller(), []);
  useEffect(() => startBacktestBackgroundPoller(), []);

  return (
    <div className={`app-shell ${sidebarCollapsed ? "app-shell--collapsed" : ""}`}>
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((v) => !v)}
      />
      <main className="app-main">
        <OhlcvExpandBanner />
        <BacktestBanner />
        <Routes>
          <Route path="/" element={<ScreenerPage />} />
          <Route path="/health" element={<DataHealthPanel />} />
          <Route path="/diagnostics" element={<DiagnosticsPanel />} />
          <Route path="/diagnostics/issues" element={<DiagnosticsPanel />} />
          <Route path="/diagnostics/issues/:issueId" element={<DiagnosticsPanel />} />
          <Route path="/strategies" element={<StrategiesPanel />} />
          <Route path="/bos-research" element={<BosResearchPanel />} />
          <Route path="/bos-strategy-research" element={<BosStrategyResearchPanel />} />
          <Route path="/paper" element={<PaperTradePanel />} />
          <Route path="/watchlist" element={<Placeholder title="Watchlist" />} />
          <Route path="/alerts" element={<AlertsPanel />} />
          <Route path="/charts" element={<ChartsPage />} />
          <Route path="/backtest" element={<BacktestPanel />} />
          <Route path="/ohlcv-history" element={<OhlcvHistoryPanel />} />
          <Route path="/settings" element={<Placeholder title="Settings" />} />
        </Routes>
      </main>
    </div>
  );
}
