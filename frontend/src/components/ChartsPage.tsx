import { useEffect } from "react";
import { BottomCharts } from "./BottomCharts";
import { CoinDetailPanel } from "./CoinDetailPanel";
import { useMarketStream } from "../hooks/useMarketStream";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { useMarketStore } from "../store/marketStore";

export function ChartsPage() {
  useMarketStream();
  const selected = useMarketStore((s) => s.selectedSymbol);
  const setSelected = useMarketStore((s) => s.setSelected);
  const ordered = useMarketStore((s) => s.orderedSymbols);
  const isNarrow = useMediaQuery("(max-width: 1099px)");

  useEffect(() => {
    if (!selected && ordered.length > 0) {
      setSelected(ordered[0]);
    }
  }, [selected, ordered, setSelected]);

  return (
    <div className="charts-page">
      <div className="charts-page-main">
        <BottomCharts variant="page" showSizeToggle={false} />
      </div>
      {!isNarrow ? (
        <aside className="charts-page-detail">
          <CoinDetailPanel />
        </aside>
      ) : null}
    </div>
  );
}
