"""CANDLE12_V1_TIME48_PROGRESS_GUARD. Research-only exit overlay.

Parent book: the frozen CANDLE12_V2 BTCUSDT 1h LONG walk that the execution
study records as 95 trades and +$594.85. Entries, the structural stop, TP1,
and which trades the shared long/short lock selected are taken from
``run_candle12_v2`` and are not recomputed here.

Two overlays, each a flag:

- TIME_STOP_48. If neither the stop nor a target has filled by the close of
  bar 48 of the trade (the entry bar is bar 1), exit at that bar's close.
  This is the same bar the loss-analysis re-walk calls a 48-bar time stop
  (``hold_last = entry_index + 48 - 1``).
- PROGRESS_GUARD_1R. After a completed bar whose high has traded +1.0R and
  that bar did not itself stop or target out, the protective stop for later
  bars is breakeven after fees and exit slippage. It is never looser than
  that price, and it does not trail. No partial exit.

The entry bar is resolved with the frozen intrabar rule before either overlay
can exit. A bar that trades both the active stop and a target still exits at
the stop. Funding stays off, matching the baseline. Nothing in this module
places an order or starts a paper book.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.research.candle12_v2 import Candle12V2Config, _exit_fill
from app.research.combination_backtest import resolve_intrabar_outcome
from app.research.config import AMBIGUOUS_CONSERVATIVE
from app.research.trade_fees import enrich_trade_execution

STRATEGY_ID = "CANDLE12_V1_TIME48_PROGRESS_GUARD"
PARENT_STRATEGY_ID = "CANDLE12_V2"
ENGINE_VERSION = "1.0.0"
TIME_STOP_BARS = 48
PROGRESS_R = 1.0
OUTCOME_TIME_STOP = "TIME_STOP_48"
OUTCOME_PROGRESS_GUARD = "PROGRESS_GUARD"
GAVE_BACK_FRACTION = 0.5
LATE_STOP_DRIFT_BARS = 10

RISK_USD = 20.0
ACCOUNT_EQUITY = 1000.0
LEVERAGE = 2.0
TAKER_FEE = 0.0004
MAKER_FEE = 0.0002
ENTRY_SLIPPAGE = 0.0002
EXIT_SLIPPAGE = 0.0002
FUNDING_ENABLED = False

# Decision thresholds. Fixed before the run; not fit to the result.
NET_R_MATERIAL = 0.5
DD_R_MATERIAL = 0.25
MAX_WINNERS_LOST = 1
# A scratch within one cent is not a win. Breakeven-after-fees lands here.
WIN_NET_USD = 0.01

DISCLAIMER = (
    "In-sample research only. Not a profitability claim, not an edge claim, "
    "and not production or paper trading."
)

MFE_BUCKETS = (
    ("<0", None, 0.0),
    ("0 to 0.5", 0.0, 0.5),
    ("0.5 to 1", 0.5, 1.0),
    ("1 to 2", 1.0, 2.0),
    ("2 to 3", 2.0, 3.0),
    (">3", 3.0, None),
)
HOLD_BUCKETS = (
    ("0", 0, 0),
    ("1-5", 1, 5),
    ("6-12", 6, 12),
    ("13-24", 13, 24),
    ("25-47", 25, 47),
    ("48+", 48, None),
)


@dataclass(frozen=True)
class OverlayConfig:
    """The only two switches. Bar count and the 1R level are constants."""

    time_stop_48: bool = True
    progress_guard_1r: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": STRATEGY_ID,
            "engine_version": ENGINE_VERSION,
            "parent_strategy_id": PARENT_STRATEGY_ID,
            "time_stop_48": self.time_stop_48,
            "progress_guard_1r": self.progress_guard_1r,
            "time_stop_bars": TIME_STOP_BARS,
            "progress_r": PROGRESS_R,
            "risk_usd": RISK_USD,
            "account_equity": ACCOUNT_EQUITY,
            "leverage": LEVERAGE,
            "taker_fee": TAKER_FEE,
            "maker_fee": MAKER_FEE,
            "entry_slippage": ENTRY_SLIPPAGE,
            "exit_slippage": EXIT_SLIPPAGE,
            "funding_enabled": FUNDING_ENABLED,
            "book": "BTCUSDT 1h LONG, shared lock inherited from CANDLE12_V2",
        }


def config_hash(cfg: OverlayConfig) -> str:
    payload = json.dumps(cfg.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def breakeven_raw(
    entry: float,
    *,
    direction: str = "LONG",
    taker_fee: float = TAKER_FEE,
    exit_slippage: float = EXIT_SLIPPAGE,
) -> float:
    """Raw stop whose slipped fill nets zero after both taker fees.

    Dollar net is ``qty * (fill - entry) - fee * qty * (entry + fill)`` for a
    long, with ``fill = raw * (1 - exit_slippage)``. Solving net = 0 gives
    ``raw = entry * (1 + fee) / ((1 - fee) * (1 - exit_slippage))``. A short
    mirrors the slippage sign.
    """
    if not 0.0 <= taker_fee < 1.0 or not 0.0 <= exit_slippage < 1.0:
        raise ValueError("fee and exit slippage must be in [0, 1)")
    fill = entry * (1.0 + taker_fee) / (1.0 - taker_fee)
    if direction == "LONG":
        return fill / (1.0 - exit_slippage)
    return fill / (1.0 + exit_slippage)


def protective_stop(structural_stop: float, be_raw: float, direction: str) -> float:
    """Breakeven stop that is never worse than breakeven-after-fees.

    For a long, a lower price is a worse stop. For a short, a higher price is.
    """
    if direction == "LONG":
        return max(structural_stop, be_raw)
    return min(structural_stop, be_raw)


def _targets(trade: Mapping[str, Any]) -> list[float]:
    out: list[float] = []
    for key in ("tp1", "tp2", "tp3"):
        value = trade.get(key)
        if value is not None:
            out.append(float(value))
    return out


def _bar_number(index: int, entry_index: int) -> int:
    return index - entry_index + 1


def _reached_progress(high: float, low: float, entry: float, risk: float, direction: str) -> bool:
    if risk <= 0:
        return False
    if direction == "LONG":
        return high >= entry + PROGRESS_R * risk
    return low <= entry - PROGRESS_R * risk


def _ohlc(candle: Mapping[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(candle["open"]),
        float(candle["high"]),
        float(candle["low"]),
        float(candle["close"]),
    )


@dataclass(frozen=True)
class ReplayExit:
    exit_index: int | None
    raw_exit: float | None
    outcome: str
    ambiguous: bool
    guard_activated: bool
    protective_stop: float
    holding_bars: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def replay_exit(
    trade: Mapping[str, Any],
    candles: Sequence[Mapping[str, Any]],
    overlay: OverlayConfig,
    *,
    v2: Candle12V2Config | None = None,
) -> ReplayExit:
    """Re-walk one already-selected trade. Does not open any other trade.

    Bars after the exit are not read. The progress guard, once armed, applies
    on the next bar only.
    """
    cfg = v2 or Candle12V2Config()
    if cfg.ambiguous_handling != AMBIGUOUS_CONSERVATIVE:
        raise ValueError("overlay replays the frozen conservative same-bar rule only")
    direction = str(trade["direction"]).upper()
    if direction != "LONG":
        raise ValueError("this research book replays LONG trades only")
    entry_index = int(trade["entry_index"])
    entry = float(trade["entry_price"])
    structural = float(trade["stop_price"])
    risk = abs(entry - structural)
    targets = _targets(trade)
    be = breakeven_raw(entry, direction=direction, taker_fee=cfg.trading_fee, exit_slippage=cfg.exit_slippage)
    stop = structural
    guard = False
    n = len(candles)

    for i in range(entry_index, n):
        _o, high, low, close = _ohlc(candles[i])
        outcome, raw, ambiguous = resolve_intrabar_outcome(
            direction=direction,
            high=high,
            low=low,
            stop=stop,
            targets=targets,
            handling=cfg.ambiguous_handling,
        )
        if outcome is not None:
            tagged = outcome
            if (
                guard
                and outcome == "SL"
                and raw is not None
                and abs(float(raw) - stop) <= 1e-9
                and stop != structural
            ):
                tagged = OUTCOME_PROGRESS_GUARD
            return ReplayExit(
                exit_index=i,
                raw_exit=float(raw if raw is not None else stop),
                outcome=tagged,
                ambiguous=bool(ambiguous),
                guard_activated=guard,
                protective_stop=stop,
                holding_bars=i - entry_index,
            )
        if overlay.time_stop_48 and _bar_number(i, entry_index) == TIME_STOP_BARS:
            return ReplayExit(
                exit_index=i,
                raw_exit=close,
                outcome=OUTCOME_TIME_STOP,
                ambiguous=False,
                guard_activated=guard,
                protective_stop=stop,
                holding_bars=i - entry_index,
            )
        if (
            overlay.progress_guard_1r
            and not guard
            and _reached_progress(high, low, entry, risk, direction)
        ):
            guard = True
            stop = protective_stop(structural, be, direction)
    return ReplayExit(
        exit_index=None,
        raw_exit=None,
        outcome="OPEN",
        ambiguous=False,
        guard_activated=guard,
        protective_stop=stop,
        holding_bars=None,
    )


def _wilder_atr(candles: Sequence[Mapping[str, Any]], period: int = 14) -> list[float | None]:
    n = len(candles)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    tr = [highs[0] - lows[0]]
    for i in range(1, n):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    atr = sum(tr[1 : period + 1]) / period
    out[period] = atr
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + tr[i]) / period
        out[i] = atr
    return out


def _path_metrics(
    trade: Mapping[str, Any],
    candles: Sequence[Mapping[str, Any]],
    replay: ReplayExit,
    atr: Sequence[float | None],
) -> dict[str, Any]:
    entry_index = int(trade["entry_index"])
    entry = float(trade["entry_price"])
    stop = float(trade["stop_price"])
    risk = abs(entry - stop)
    exit_index = replay.exit_index if replay.exit_index is not None else entry_index
    best = 0.0
    best_i: int | None = None
    worst = 0.0
    for i in range(entry_index, exit_index):
        fav = float(candles[i]["high"]) - entry
        adv = entry - float(candles[i]["low"])
        if fav > best:
            best, best_i = fav, i
        worst = max(worst, adv)
    if replay.raw_exit is not None:
        worst = max(worst, entry - float(replay.raw_exit))
        best_at_exit = max(best, float(replay.raw_exit) - entry)
    else:
        best_at_exit = best
    if replay.exit_index is not None and replay.exit_index < len(candles):
        worst = max(worst, entry - float(candles[replay.exit_index]["low"]))
    tp1 = trade.get("tp1")
    dist = abs(float(tp1) - entry) if tp1 is not None else None
    reached_half = None if dist is None or dist <= 0 or risk <= 0 else best >= GAVE_BACK_FRACTION * dist
    hold = replay.holding_bars
    late = False
    if (
        replay.outcome in ("SL", OUTCOME_PROGRESS_GUARD)
        and best_i is not None
        and best > 0
        and hold is not None
        and (best_i - entry_index) <= hold / 3.0
        and (exit_index - best_i) >= LATE_STOP_DRIFT_BARS
    ):
        late = True
    atr_i = entry_index - 1
    atr_value = atr[atr_i] if 0 <= atr_i < len(atr) else None
    return {
        "mfe_r_before_exit": (best / risk) if risk else None,
        "mfe_r_at_exit": (best_at_exit / risk) if risk else None,
        "mae_r": (worst / risk) if risk else None,
        "reached_half_to_tp1": reached_half,
        "late_stop": late,
        "stop_distance_atr": (risk / atr_value) if atr_value else None,
        "bars_to_mfe": (best_i - entry_index) if best_i is not None else None,
    }


def loss_reason(row: Mapping[str, Any]) -> str | None:
    """One primary bucket. Exit overlays outrank the path labels.

    Path labels apply only to losing trades. ``reached_half_to_tp1`` is half
    the entry-to-TP1 distance, the same cut the loss study uses for
    "gave back". "Stopped before any real progress" is a loss that never
    reached that half. The prior-24-bar range tag is not a bucket and is not
    a filter.
    """
    outcome = str(row.get("outcome") or "")
    if outcome == OUTCOME_TIME_STOP:
        return "time-stop exit"
    if outcome == OUTCOME_PROGRESS_GUARD:
        return "progress-guard exit"
    net = row.get("net_pnl_usd")
    if net is None or float(net) >= 0:
        return None
    if row.get("reached_half_to_tp1") is True:
        return "gave back open profit"
    if row.get("late_stop") is True:
        return "setup failed early, stop hit late"
    if row.get("reached_half_to_tp1") is False:
        return "stopped before any real progress"
    return "unclassified loss"


def apply_overlay(
    trades: Sequence[Mapping[str, Any]],
    candles: Sequence[Mapping[str, Any]],
    overlay: OverlayConfig,
    *,
    v2: Candle12V2Config | None = None,
) -> list[dict[str, Any]]:
    """Exit-overlay the frozen trade list. The list itself is not filtered."""
    cfg = v2 or Candle12V2Config(
        trading_fee=TAKER_FEE,
        entry_slippage=ENTRY_SLIPPAGE,
        exit_slippage=EXIT_SLIPPAGE,
    )
    atr = _wilder_atr(candles)
    rows: list[dict[str, Any]] = []
    for trade in trades:
        replay = replay_exit(trade, candles, overlay, v2=cfg)
        if replay.outcome == "OPEN" or replay.exit_index is None or replay.raw_exit is None:
            continue
        path = _path_metrics(trade, candles, replay, atr)
        fill = _exit_fill(str(trade["direction"]), float(replay.raw_exit), cfg)
        enriched = enrich_trade_execution(
            {
                "direction": trade["direction"],
                "entry_price": trade["entry_price"],
                "stop_price": trade["stop_price"],
                "exit_price": fill,
                "raw_exit_price": replay.raw_exit,
                "entry_time": trade.get("signal_time"),
                "signal_time": trade.get("signal_time"),
                "exit_time": candles[replay.exit_index].get("time"),
                "entry_index": trade["entry_index"],
                "exit_index": replay.exit_index,
                "outcome": replay.outcome,
                "tp1": trade.get("tp1"),
                "tp2": trade.get("tp2"),
                "tp3": trade.get("tp3"),
                "holding_bars": replay.holding_bars,
                "ambiguous": replay.ambiguous,
                "condition_snapshot": {"entry_type": "MARKET"},
            },
            risk_usd=RISK_USD,
            taker_fee=cfg.trading_fee,
            maker_fee=MAKER_FEE,
            leverage=LEVERAGE,
            account_equity=ACCOUNT_EQUITY,
        )
        enriched.update(path)
        enriched["guard_activated"] = replay.guard_activated
        enriched["protective_stop"] = replay.protective_stop
        enriched["time_stop_exit"] = replay.outcome == OUTCOME_TIME_STOP
        enriched["progress_guard_exit"] = replay.outcome == OUTCOME_PROGRESS_GUARD
        enriched["funding_usd"] = 0.0
        enriched["fee_entry_rate_applied"] = cfg.trading_fee
        enriched["exit_slippage_applied"] = cfg.exit_slippage
        enriched["loss_reason"] = loss_reason(enriched)
        enriched["strategy_id"] = STRATEGY_ID
        enriched["overlay"] = overlay.to_dict()
        rows.append(enriched)
    rows.sort(key=lambda t: (str(t.get("exit_time") or ""), int(t["entry_index"])))
    return rows


def baseline_identity(
    parent_trades: Sequence[Mapping[str, Any]],
    replayed: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Both overlays off must reproduce the parent exit, price, and dollar net."""
    by_entry = {int(t["entry_index"]): t for t in replayed}
    mismatches: list[dict[str, Any]] = []
    for trade in parent_trades:
        if str(trade.get("outcome") or "") in ("", "OPEN") or trade.get("exit_price") is None:
            continue
        if str(trade.get("direction") or "").upper() != "LONG":
            continue
        got = by_entry.get(int(trade["entry_index"]))
        if got is None:
            mismatches.append({"entry_index": trade["entry_index"], "why": "missing"})
            continue
        same_outcome = str(got["outcome"]) == str(trade["outcome"])
        same_index = int(got["exit_index"]) == int(trade["exit_index"])
        same_raw = abs(float(got["raw_exit_price"]) - float(trade["exit_price"])) <= 1e-6
        if not (same_outcome and same_index and same_raw):
            mismatches.append(
                {
                    "entry_index": int(trade["entry_index"]),
                    "parent_outcome": trade.get("outcome"),
                    "replay_outcome": got["outcome"],
                    "parent_exit_index": trade.get("exit_index"),
                    "replay_exit_index": got["exit_index"],
                    "parent_raw": trade.get("exit_price"),
                    "replay_raw": got.get("raw_exit_price"),
                }
            )
    parent_closed = [
        t
        for t in parent_trades
        if str(t.get("direction") or "").upper() == "LONG"
        and str(t.get("outcome") or "") not in ("", "OPEN")
        and t.get("exit_price") is not None
    ]
    return {
        "parent_closed_longs": len(parent_closed),
        "replayed": len(replayed),
        "mismatches": mismatches,
        "ok": len(mismatches) == 0 and len(replayed) == len(parent_closed),
    }


def _as_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _max_drawdown(values: Sequence[float], start: float = 0.0) -> float:
    eq = peak = start
    dd = 0.0
    for v in values:
        eq += v
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return dd


def _bucket(value: float | None, spec: Sequence[tuple[str, Any, Any]], *, hold: bool = False) -> str:
    if value is None:
        return "unknown"
    for label, lo, hi in spec:
        if hold:
            if hi is None and value >= lo:
                return label
            if lo == hi and value == lo:
                return label
            if hi is not None and lo is not None and lo <= value <= hi and lo != hi:
                return label
        else:
            if lo is None and hi is not None and value < hi:
                return label
            if lo is not None and hi is None and value >= lo:
                return label
            if lo is not None and hi is not None and lo <= value < hi:
                return label
    return "unknown"


def _losing_streak(nets: Sequence[float]) -> int:
    best = cur = 0
    for n in nets:
        if n < -WIN_NET_USD:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def summarize(rows: Sequence[Mapping[str, Any]], *, baseline: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda t: (str(t.get("exit_time") or ""), int(t.get("entry_index") or 0)))
    nets = [float(t["net_pnl_usd"]) for t in ordered]
    rs = [float(t["r_net"]) for t in ordered if t.get("r_net") is not None]
    wins = [p for p in nets if p > WIN_NET_USD]
    losses = [p for p in nets if p < -WIN_NET_USD]
    win_r = [float(t["r_net"]) for t in ordered if float(t["net_pnl_usd"]) > WIN_NET_USD and t.get("r_net") is not None]
    loss_r = [float(t["r_net"]) for t in ordered if float(t["net_pnl_usd"]) < -WIN_NET_USD and t.get("r_net") is not None]
    n = len(ordered)
    reasons: dict[str, int] = {
        "stopped before any real progress": 0,
        "setup failed early, stop hit late": 0,
        "gave back open profit": 0,
        "time-stop exit": 0,
        "progress-guard exit": 0,
        "unclassified loss": 0,
    }
    for t in ordered:
        reason = t.get("loss_reason")
        if reason in reasons:
            reasons[reason] += 1
    mfe_hist = {label: 0 for label, _, _ in MFE_BUCKETS}
    hold_hist = {label: 0 for label, _, _ in HOLD_BUCKETS}
    for t in ordered:
        mfe_hist[_bucket(None if t.get("mfe_r_at_exit") is None else float(t["mfe_r_at_exit"]), MFE_BUCKETS)] += 1
        hold_hist[_bucket(None if t.get("holding_bars") is None else float(t["holding_bars"]), HOLD_BUCKETS, hold=True)] += 1
    by_year: dict[str, list[Mapping[str, Any]]] = {}
    by_month: dict[str, float] = {}
    for t in ordered:
        ts = _as_dt(t.get("exit_time"))
        if ts is None:
            continue
        by_year.setdefault(str(ts.year), []).append(t)
        key = f"{ts.year:04d}-{ts.month:02d}"
        by_month[key] = by_month.get(key, 0.0) + float(t["net_pnl_usd"])
    years = {}
    for year, group in sorted(by_year.items()):
        gnets = [float(t["net_pnl_usd"]) for t in group]
        grs = [float(t["r_net"]) for t in group if t.get("r_net") is not None]
        years[year] = {
            "trades": len(group),
            "net_pnl_usd": round(sum(gnets), 2),
            "end_equity_usd": round(ACCOUNT_EQUITY + sum(gnets), 2),
            "max_drawdown_usd": round(_max_drawdown(gnets, ACCOUNT_EQUITY), 2),
            "net_r": round(sum(grs), 4),
            "max_drawdown_r": round(_max_drawdown(grs, 0.0), 4),
        }
    worst = sorted(ordered, key=lambda t: float(t["net_pnl_usd"]))[:10]
    base_by_entry = {int(t["entry_index"]): t for t in (baseline or [])}
    winners_lost = 0
    if baseline is not None:
        for t in ordered:
            prev = base_by_entry.get(int(t["entry_index"]))
            if prev is None:
                continue
            if float(prev["net_pnl_usd"]) > WIN_NET_USD and float(t["net_pnl_usd"]) <= WIN_NET_USD:
                winners_lost += 1
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "trades": n,
        "net_pnl_usd": round(sum(nets), 2),
        "net_r": round(sum(rs), 4),
        "net_r_intended_20": round(sum(nets) / RISK_USD, 4) if n else 0.0,
        "win_rate": round(len(wins) / n, 4) if n else None,
        "avg_win_r": round(sum(win_r) / len(win_r), 4) if win_r else None,
        "avg_loss_r": round(sum(loss_r) / len(loss_r), 4) if loss_r else None,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss else None,
        "max_drawdown_usd": round(_max_drawdown(nets, ACCOUNT_EQUITY), 2),
        "max_drawdown_r": round(_max_drawdown(rs, 0.0), 4),
        "longest_losing_streak": _losing_streak(nets),
        "winners_lost_vs_baseline": winners_lost if baseline is not None else None,
        "time_stop_exits": sum(1 for t in ordered if t.get("time_stop_exit")),
        "progress_guard_activated": sum(1 for t in ordered if t.get("guard_activated")),
        "progress_guard_exits": sum(1 for t in ordered if t.get("progress_guard_exit")),
        "mfe_at_exit": mfe_hist,
        "holding_bars": hold_hist,
        "loss_reasons": reasons,
        "per_year": years,
        "monthly_net_pnl_usd": {k: round(v, 2) for k, v in sorted(by_month.items())},
        "worst_10": [
            {
                "entry_time": str(t.get("entry_time")),
                "exit_time": str(t.get("exit_time")),
                "outcome": t.get("outcome"),
                "loss_reason": t.get("loss_reason"),
                "net_pnl_usd": round(float(t["net_pnl_usd"]), 2),
                "r_net": None if t.get("r_net") is None else round(float(t["r_net"]), 4),
                "mfe_r_at_exit": None if t.get("mfe_r_at_exit") is None else round(float(t["mfe_r_at_exit"]), 4),
                "mae_r": None if t.get("mae_r") is None else round(float(t["mae_r"]), 4),
                "holding_bars": t.get("holding_bars"),
                "stop_distance_atr": None
                if t.get("stop_distance_atr") is None
                else round(float(t["stop_distance_atr"]), 4),
                "fees_usd": round(float(t.get("fee_total_usd") or 0.0), 4),
                "entry_index": t.get("entry_index"),
            }
            for t in worst
        ],
        "fees_usd": round(sum(float(t.get("fee_total_usd") or 0.0) for t in ordered), 2),
        "funding_usd": round(sum(float(t.get("funding_usd") or 0.0) for t in ordered), 2),
    }


def classify_variant(summary: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    """PROMISING / NEUTRAL / REJECT from the predeclared rule. In-sample only."""
    net = float(summary["net_r"]) - float(baseline["net_r"])
    dd = float(summary["max_drawdown_r"]) - float(baseline["max_drawdown_r"])
    lost = int(summary.get("winners_lost_vs_baseline") or 0)
    reasons: list[str] = []
    if net <= -NET_R_MATERIAL:
        reasons.append("net R fell by at least 0.5R")
    if dd >= DD_R_MATERIAL:
        reasons.append("max drawdown in R rose by at least 0.25R")
    if lost > MAX_WINNERS_LOST:
        reasons.append(f"{lost} baseline winners finished flat or worse")
    if reasons:
        label = "REJECT"
    elif net >= NET_R_MATERIAL and dd <= -DD_R_MATERIAL and lost <= MAX_WINNERS_LOST:
        label = "PROMISING"
        reasons.append("net R up by at least 0.5R, max R drawdown down by at least 0.25R, at most one winner lost")
    else:
        label = "NEUTRAL"
        reasons.append("change is inside the materiality band, or only one of net R and R drawdown improved")
    return {
        "label": label,
        "delta_net_r": round(net, 4),
        "delta_max_drawdown_r": round(dd, 4),
        "winners_lost": lost,
        "reasons": reasons,
    }


def validate_fills(rows: Sequence[Mapping[str, Any]], candles: Sequence[Mapping[str, Any]]) -> list[str]:
    """Each exit raw price is a stop, a target, or that bar's close. No other price."""
    errors: list[str] = []
    for row in rows:
        idx = int(row["exit_index"])
        entry_index = int(row["entry_index"])
        if idx < entry_index or idx >= len(candles):
            errors.append(f"exit index {idx} outside the walk")
            continue
        raw = float(row["raw_exit_price"])
        outcome = str(row["outcome"])
        if outcome == OUTCOME_TIME_STOP:
            if idx != entry_index + TIME_STOP_BARS - 1:
                errors.append(f"time stop at {idx}, expected bar {TIME_STOP_BARS}")
            if abs(raw - float(candles[idx]["close"])) > 1e-6:
                errors.append("time stop fill is not that bar's close")
        elif outcome == OUTCOME_PROGRESS_GUARD:
            if abs(raw - float(row["protective_stop"])) > 1e-6:
                errors.append("progress-guard fill is not the breakeven stop")
            if float(row["protective_stop"]) + 1e-9 < breakeven_raw(float(row["entry_price"])):
                errors.append("progress-guard stop is worse than breakeven-after-fees")
        elif outcome == "SL":
            if abs(raw - float(row["stop_price"])) > 1e-6:
                errors.append("stop fill is not the structural stop")
        elif outcome.startswith("TP"):
            targets = _targets(row)
            if not any(abs(raw - tp) <= 1e-6 for tp in targets):
                errors.append(f"{outcome} fill is not a target")
        else:
            errors.append(f"unexpected outcome {outcome}")
        if outcome == OUTCOME_PROGRESS_GUARD and not row.get("guard_activated"):
            errors.append("progress-guard exit without activation")
        if row.get("fee_entry_rate_applied") != TAKER_FEE or row.get("funding_usd") not in (0, 0.0):
            errors.append("fee or funding drifted from the baseline")
    return errors


def dollar_identity(parent_enriched: Sequence[Mapping[str, Any]], replayed: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Dollar nets with both overlays off against the adapter's own enrichment."""
    by_entry = {int(t["entry_index"]): t for t in replayed}
    worst = 0.0
    missing = 0
    for trade in parent_enriched:
        got = by_entry.get(int(trade["entry_index"]))
        if got is None:
            missing += 1
            continue
        worst = max(worst, abs(float(got["net_pnl_usd"]) - float(trade["net_pnl_usd"])))
    return {"missing": missing, "max_abs_net_diff_usd": worst, "ok": missing == 0 and worst <= 1e-6}
