"""Historical analysis using the same SignalEngine as live mode.

Results are historical analysis only — not proof of future profitability.
Do not optimize parameters on the same dataset used to evaluate them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.signals.config import SignalConfig
from app.signals.signal_engine import SignalEngine
from app.signals.schemas import SignalStatus
from app.signals.trade_math import parse_direction_optional


@dataclass
class BacktestTrade:
    entry_index: int
    direction: str
    entry: float
    stop: float
    targets: list[float]
    exit_index: int | None = None
    exit_price: float | None = None
    outcome: str | None = None  # STOP | TP1 | TP2 | TP3
    r_multiple: float | None = None
    holding_bars: int | None = None


@dataclass
class BacktestReport:
    symbol: str
    timeframe: str
    signals: int = 0
    entries: int = 0
    wins: int = 0
    losses: int = 0
    tp1_hit: int = 0
    tp2_hit: int = 0
    tp3_hit: int = 0
    stop_hit: int = 0
    average_r: float | None = None
    maximum_drawdown_r: float | None = None
    win_rate: float | None = None
    profit_factor: float | None = None
    expectancy: float | None = None
    average_holding_time: float | None = None
    by_direction: dict[str, Any] = field(default_factory=dict)
    disclaimer: str = (
        "Historical analysis using the live signal engine rules. "
        "Not a claim of future profitability. Parameters were not optimized on this set."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "signals": self.signals,
            "entries": self.entries,
            "wins": self.wins,
            "losses": self.losses,
            "tp1_hit": self.tp1_hit,
            "tp2_hit": self.tp2_hit,
            "tp3_hit": self.tp3_hit,
            "stop_hit": self.stop_hit,
            "average_r": self.average_r,
            "maximum_drawdown_r": self.maximum_drawdown_r,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "expectancy": self.expectancy,
            "average_holding_time": self.average_holding_time,
            "by_direction": self.by_direction,
            "disclaimer": self.disclaimer,
            "label": "HISTORICAL_ANALYSIS",
        }


def run_backtest(
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    *,
    config: SignalConfig | None = None,
    higher_tf_candles: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    min_bars: int = 50,
) -> BacktestReport:
    """Walk forward bar-by-bar with as_of_index — no look-ahead."""
    cfg = config or SignalConfig()
    engine = SignalEngine(cfg)
    report = BacktestReport(symbol=symbol.upper(), timeframe=timeframe)
    trades: list[BacktestTrade] = []
    open_trade: BacktestTrade | None = None
    equity_curve_r = [0.0]
    rs: list[float] = []

    n = len(candles)
    if n < min_bars:
        return report

    for i in range(min_bars, n):
        # Manage open trade with current bar only
        if open_trade is not None:
            c = candles[i]
            high = float(c.get("high") or c.get("h") or 0)
            low = float(c.get("low") or c.get("l") or 0)
            hit = None
            exit_px = None
            if open_trade.direction == "LONG":
                if low <= open_trade.stop:
                    hit, exit_px = "STOP", open_trade.stop
                else:
                    for ti, tp in enumerate(open_trade.targets):
                        if high >= tp:
                            hit, exit_px = f"TP{ti+1}", tp
                            break
            else:
                if high >= open_trade.stop:
                    hit, exit_px = "STOP", open_trade.stop
                else:
                    for ti, tp in enumerate(open_trade.targets):
                        if low <= tp:
                            hit, exit_px = f"TP{ti+1}", tp
                            break
            if hit:
                risk = abs(open_trade.entry - open_trade.stop)
                if open_trade.direction == "LONG":
                    r = (exit_px - open_trade.entry) / risk if risk else 0.0
                else:
                    r = (open_trade.entry - exit_px) / risk if risk else 0.0
                open_trade.exit_index = i
                open_trade.exit_price = exit_px
                open_trade.outcome = hit
                open_trade.r_multiple = r
                open_trade.holding_bars = i - open_trade.entry_index
                trades.append(open_trade)
                rs.append(r)
                equity_curve_r.append(equity_curve_r[-1] + r)
                if hit == "STOP":
                    report.stop_hit += 1
                    report.losses += 1
                else:
                    report.wins += 1
                    if hit == "TP1":
                        report.tp1_hit += 1
                    elif hit == "TP2":
                        report.tp2_hit += 1
                    elif hit == "TP3":
                        report.tp3_hit += 1
                open_trade = None
                continue

        candles_by_tf: dict[str, Sequence[Mapping[str, Any]]] = {
            timeframe: list(candles[: i + 1]),
        }
        # Higher TFs: use full history truncated — no future bars
        if higher_tf_candles:
            for tf, series in higher_tf_candles.items():
                candles_by_tf[tf] = list(series)  # assumed pre-aligned closed only
        else:
            # Mirror setup TF into higher roles when only one series provided
            candles_by_tf[cfg.mtf_major] = list(candles[: i + 1])
            candles_by_tf[cfg.mtf_primary] = list(candles[: i + 1])
            candles_by_tf[cfg.mtf_setup] = list(candles[: i + 1])
            candles_by_tf[cfg.mtf_entry] = list(candles[: i + 1])

        as_of = {tf: len(series) - 1 for tf, series in candles_by_tf.items() if series}
        analysis = engine.analyze(
            symbol,
            candles_by_tf,
            as_of_index_by_tf=as_of,
        )
        if analysis.status in (
            SignalStatus.LONG_ENTRY_CANDIDATE.value,
            SignalStatus.SHORT_ENTRY_CANDIDATE.value,
            SignalStatus.ENTRY_CANDIDATE.value,
        ):
            report.signals += 1
            if open_trade is None and analysis.entry and analysis.stop:
                ep = analysis.entry.get("entry_price")
                sp = analysis.stop.get("final_stop")
                tps = [float(t["target_price"]) for t in analysis.targets if t.get("target_price")]
                if ep is not None and sp is not None and tps:
                    parsed = parse_direction_optional(analysis.direction)
                    # SHORT research path: never invent LONG from a missing direction.
                    if analysis.status == SignalStatus.SHORT_ENTRY_CANDIDATE.value:
                        if parsed is None or parsed.value != "SHORT":
                            continue
                        trade_direction = "SHORT"
                    else:
                        # Explicit LONG default only for non-SHORT entry statuses
                        # (preserves historical LONG backtest callers).
                        trade_direction = parsed.value if parsed is not None else "LONG"
                    report.entries += 1
                    open_trade = BacktestTrade(
                        entry_index=i,
                        direction=trade_direction,
                        entry=float(ep),
                        stop=float(sp),
                        targets=tps,
                    )

    report.average_r = (sum(rs) / len(rs)) if rs else None
    report.win_rate = (report.wins / report.entries) if report.entries else None
    gains = [r for r in rs if r > 0]
    losses = [abs(r) for r in rs if r <= 0]
    report.profit_factor = (sum(gains) / sum(losses)) if losses and sum(losses) > 0 else None
    report.expectancy = report.average_r
    holds = [t.holding_bars for t in trades if t.holding_bars is not None]
    report.average_holding_time = (sum(holds) / len(holds)) if holds else None
    # Max drawdown in R
    peak = 0.0
    max_dd = 0.0
    for e in equity_curve_r:
        peak = max(peak, e)
        max_dd = min(max_dd, e - peak)
    report.maximum_drawdown_r = abs(max_dd) if equity_curve_r else None
    report.by_direction = {
        "LONG": {
            "entries": sum(1 for t in trades if t.direction == "LONG"),
            "wins": sum(1 for t in trades if t.direction == "LONG" and (t.r_multiple or 0) > 0),
        },
        "SHORT": {
            "entries": sum(1 for t in trades if t.direction == "SHORT"),
            "wins": sum(1 for t in trades if t.direction == "SHORT" and (t.r_multiple or 0) > 0),
        },
    }
    return report
