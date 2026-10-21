"""Deterministic paper-engine replay used by the D128d flag-off identity test.

The same file runs on the base commit (b009aca, before kill switches existed) and on the kill-switch branch. It
drives the real PaperTradeEngine through 48 hourly bars on three coins: passed-book, experimental and V2.1-A
opens, live-mark exits, causal closed-bar exits, the risk gate's refusals and the latching stops. Every call's
outcome (the position booked, or the skip reason) is recorded; ``canonical_json(run_scenario())`` is the decision
journal compared byte for byte.

Paper only: market inputs are stubbed, no database, no network.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import app.services.paper_risk as pr
import app.services.paper_trade as paper_mod
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperTradeEngine, is_causal_passed

BAR0 = datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
BASE = {"BTCUSDT": 60000.0, "ETHUSDT": 3000.0, "SOLUSDT": 150.0}
GROUPS = {"BTCUSDT": ("BTC", None, "WAITING"), "ETHUSDT": ("ETH", None, "WAITING"),
          "SOLUSDT": ("large-cap", 5.0e10, "LIVE")}
# Wall-clock stamps and random ids: not decisions, and different on every run.
DROP = {"id", "opened_at", "timestamp", "paper_entry_time", "trade_id"}


def _price(sym: str, i: int) -> float:
    k = sorted(BASE).index(sym)
    step = ((i * 7 + k * 3) % 11) - 5  # -5..5
    trend = ((i // 12) % 2) * 2 - 1  # alternating 12-bar drifts
    return round(BASE[sym] * (1.0 + 0.006 * step + 0.002 * trend * (i % 12)), 6)


def _clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in sorted(obj.items()) if k not in DROP}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, float):
        return round(obj, 8)
    return obj


def _policy() -> PaperRiskPolicy:
    return PaperRiskPolicy(
        enabled=True,
        max_open_positions=4,
        max_open_risk_pct=0.08,
        liq_gate_enabled=False,
        daily_loss_halt_r=99.0,
        consecutive_loss_halt=3,
        consecutive_loss_symbol_halt=2,
        peak_drawdown_halt_pct=0.99,
        strategy_drawdown_halt_r=4.0,
    )


def _bars_for(eng: PaperTradeEngine, sym: str, upto: int) -> list[dict[str, Any]]:
    """Closed 1h bars from each causal position's fill bar up to bar ``upto`` (inclusive)."""
    out = []
    for pos in list(eng._open.values()):  # noqa: SLF001
        if pos.symbol != sym or not is_causal_passed(pos):
            continue
        t = datetime.fromisoformat(pos.signal_snippet["fill_bar_time"])
        while t <= BAR0 + upto * HOUR:
            j = int((t - BAR0) / HOUR)
            o, c = _price(sym, max(j - 1, 0)), _price(sym, j)
            out.append({"time": t, "open": o, "high": max(o, c) * 1.004, "low": min(o, c) * 0.996, "close": c})
            t += HOUR
        break
    return out


def run_scenario(attach: Callable[[PaperTradeEngine], None] | None = None) -> list[dict[str, Any]]:
    saved = (pr._mcap_and_group, pr._quote_volume_24h, paper_mod._live_price_with_source)  # noqa: SLF001
    pr._mcap_and_group = lambda s: GROUPS.get(s.upper(), ("UNKNOWN", None, "WAITING"))  # noqa: SLF001
    pr._quote_volume_24h = lambda _s: (1.0e9, "LIVE")  # noqa: SLF001
    paper_mod._live_price_with_source = lambda _s: (None, "unavailable")  # noqa: SLF001
    try:
        eng = PaperTradeEngine(starting_equity=1000.0, enabled=True, risk_policy=_policy())
        clock = [BAR0]
        eng._now = lambda: clock[0]  # noqa: SLF001
        if attach is not None:
            attach(eng)
        log: list[dict[str, Any]] = []
        for i in range(48):
            clock[0] = BAR0 + i * HOUR + timedelta(minutes=1)
            bar = (BAR0 + i * HOUR).isoformat()
            for n, sym in enumerate(sorted(BASE)):
                px = _price(sym, i)
                plan = {"status": "LONG_ENTRY_CANDIDATE", "direction": "LONG", "entry_price": px,
                        "stop_price": round(px * 0.985, 6), "tp1": round(px * 1.03, 6)}
                kind = (i + n) % 3
                if kind == 0:
                    pos = eng.open_passed_position(symbol=sym, combination_id="COMBO_03_TRANSITION_B", timeframe="1h",
                                                   direction="LONG", eval_result=plan, risk_percent=0.02,
                                                   setup_bar_time_utc=bar)
                elif kind == 1:
                    pos = eng.open_experimental_position(
                        symbol=sym, timeframe="1h", eval_result=plan, risk_percent=0.0025,
                        signal_snippet={"strategy_id": "COMBO_02_V2_RESEARCH", "source": "V2_CANDIDATE_PAPER_WATCHER"},
                        setup_bar_time_utc=bar, replay=False, emit_alert=False)
                else:
                    pos = eng.open_v21a_position(
                        symbol=sym, timeframe="1h", eval_result=plan, risk_percent=0.01,
                        signal_snippet={"strategy_id": "COMBO_02_V2_1_A", "source": "V21A_PAPER_WATCHER"},
                        setup_bar_time_utc=bar, replay=False, emit_alert=False)
                log.append({"i": i, "op": f"open:{kind}", "symbol": sym,
                            "pos": _clean(pos.to_dict()) if pos is not None else None,
                            "skip": None if pos is not None else eng._last_skip_reason})  # noqa: SLF001
            # Live-mark exits (non-causal books), half a bar later.
            clock[0] = BAR0 + i * HOUR + timedelta(minutes=30)
            marks = {sym: round(_price(sym, i) * (1.0 + 0.012 * (((i * 5 + n) % 5) - 2)), 6)
                     for n, sym in enumerate(sorted(BASE))}
            for pos in eng.tick(marks):
                log.append({"i": i, "op": "tick_close", "pos": _clean(pos.to_dict())})
            # Causal closed-bar exits for the passed book.
            clock[0] = BAR0 + (i + 1) * HOUR + timedelta(seconds=30)
            for sym in sorted(BASE):
                bars = _bars_for(eng, sym, i)
                if bars:
                    for pos in eng.resolve_passed_exits({eng.replay_key(sym, "1h"): bars}):
                        log.append({"i": i, "op": "bar_close", "pos": _clean(pos.to_dict())})
        book = eng.positions(closed_limit=1000)
        log.append({"op": "final", "open": _clean(book["open"]), "closed": _clean(book["closed"]),
                    "status": _clean({k: v for k, v in book["status"].items() if k != "risk_policy"}),
                    "halts": eng.halt_state(), "persist_queue": len(eng.drain_persist_queue())})
        return log
    finally:
        pr._mcap_and_group, pr._quote_volume_24h, paper_mod._live_price_with_source = saved  # noqa: SLF001


def canonical_json(log: list[dict[str, Any]]) -> str:
    return json.dumps(log, sort_keys=True, default=str, separators=(",", ":"))


if __name__ == "__main__":  # python tests/kill_switch_replay_scenario.py <out.json>   (from backend/, PYTHONPATH=.)
    import sys
    from pathlib import Path

    Path(sys.argv[1]).write_text(canonical_json(run_scenario()), encoding="utf-8")
