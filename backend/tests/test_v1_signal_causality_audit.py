"""COMBO_02 v1 live-signal audit: causality, data integrity, batch/live parity.

Real BTCUSDT and SOLUSDT 1h/4h Binance USDT-M candles (to 12 Oct 2026). Nothing
here reads the database, the network, or the wall clock.

The live side is driven the way the orchestrator drives it: one closed 1h bar
at a time, a 500-bar store window, and only 4h bars already closed at the 1h
bar's close.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.research import combination_engine as engine_mod
from app.research.bos_combinations import get_combination
from app.research.combination_backtest import _bos_break_possible, run_combination_backtest
from app.research.combination_engine import evaluate_combination_at_bar
from app.research.config import ResearchConfig
from app.services import ohlcv_store as store_mod
from app.services import v1_paper_watcher as watcher_mod
from app.services.v1_paper_watcher import V1PaperWatcher, series_integrity_fault
from app.signals.config import SignalConfig
from app.signals.swing_detector import detect_swings
from app.signals.target_engine import compute_targets

SYMBOL = "BTCUSDT"
STORE = 500
PARITY_BARS = 1500
FIXTURES = Path(__file__).parent / "fixtures"
H1 = timedelta(hours=1)
H4 = timedelta(hours=4)


def _load(tf: str, name: str = "btcusdt_{tf}_audit_2026-10-12.json") -> list[dict[str, Any]]:
    raw = json.loads((FIXTURES / name.format(tf=tf)).read_text(encoding="utf-8"))
    return [
        {
            "time": datetime.fromisoformat(t),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": v,
        }
        for t, o, h, l, c, v in raw["candles"]
    ]


@pytest.fixture(scope="module")
def h1() -> list[dict[str, Any]]:
    rows = _load("1h")
    assert all(b["time"] - a["time"] == H1 for a, b in zip(rows, rows[1:]))
    return rows


@pytest.fixture(scope="module")
def h4() -> list[dict[str, Any]]:
    rows = _load("4h")
    assert all(b["time"] - a["time"] == H4 for a, b in zip(rows, rows[1:]))
    return rows


def _closed_4h(h4: list[dict[str, Any]], bar_1h: dict[str, Any]) -> list[dict[str, Any]]:
    """4h bars fully closed when ``bar_1h`` closes."""
    decision = bar_1h["time"] + H1
    return [c for c in h4 if c["time"] + H4 <= decision]


@pytest.fixture(autouse=True)
def _fresh_watcher():
    watcher_mod.reset_v1_paper_watcher_for_tests()
    yield
    watcher_mod.reset_v1_paper_watcher_for_tests()


class _NoPaper:
    """Reports no position. Any other use of the paper engine fails the test."""

    def has_open(self, _symbol: str, _stream: str) -> bool:
        return False

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"paper engine touched: {name}")


class _CandlePaper:
    """Books watcher opens and exits them on later candles, stop first.

    The book outlives a watcher, as the paper ledger outlives the process.
    """

    def __init__(self, symbol: str = SYMBOL) -> None:
        self.symbol = symbol
        self.open: dict[str, Any] | None = None
        self.trades: dict[str, dict[str, Any]] = {}
        self.last_exit_bar: datetime | None = None
        self.closed_rows: list[dict[str, Any]] = []

    def has_open(self, _symbol: str, stream: str) -> bool:
        return stream == "V1" and self.open is not None

    def positions(self, *, closed_limit: int = 50) -> dict[str, Any]:
        return {"open": [], "closed": self.closed_rows[::-1][:closed_limit]}

    def open_v1_combo_position(self, *, eval_result, setup_bar_time_utc, **_: Any):
        bar = datetime.fromisoformat(setup_bar_time_utc)
        if self.open is not None:
            return None
        # Same rule as PaperTradeEngine._reentry_blocked_locked.
        if self.last_exit_bar is not None and bar <= self.last_exit_bar:
            return None
        self.open = {
            "signal_time": setup_bar_time_utc,
            "entry": float(eval_result["entry_price"]),
            "stop": float(eval_result["stop_price"]),
            "tp1": eval_result.get("tp1"),
            "targets": [
                float(eval_result[k])
                for k in ("tp1", "tp2", "tp3")
                if eval_result.get(k) is not None
            ],
        }
        return self.open

    def manage(self, bar: dict[str, Any]) -> bool:
        pos = self.open
        if pos is None:
            return False
        reached = [tp for tp in pos["targets"] if bar["high"] >= tp]
        if bar["low"] > pos["stop"] and not reached:
            return False
        pos["exit_time"] = bar["time"].isoformat()
        self.trades[pos["signal_time"]] = pos
        self.open = None
        self.last_exit_bar = bar["time"]
        # The ledger row: closed by a price tick inside this bar.
        self.closed_rows.append(
            {
                "symbol": self.symbol,
                "status": "CLOSED",
                "timeframe": "1h",
                "closed_at": (bar["time"] + timedelta(minutes=30)).isoformat(),
                "signal_snippet": {
                    "source": "V1_PAPER_WATCHER",
                    "strategy_id": "COMBO_02_V1",
                },
            }
        )
        return True


def _batch(
    h1: list[dict[str, Any]],
    h4: list[dict[str, Any]],
    *,
    symbol: str = SYMBOL,
    combination: str = "COMBO_02",
) -> dict[str, dict[str, Any]]:
    run = run_combination_backtest(
        symbol,
        "1h",
        h1,
        combination,
        direction_filter="LONG",
        candles_1h=h1,
        candles_4h=h4,
        index_start=STORE,
    )
    assert run["status"] == "OK"
    return {t["signal_time"]: t for t in run["trades"] if t["outcome"] != "OPEN"}


def _live(
    h1: list[dict[str, Any]],
    h4: list[dict[str, Any]],
    *,
    symbol: str = SYMBOL,
    restart_at: frozenset[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Walk ``on_closed_1h`` one closed bar at a time. Returns trades and skips.

    ``restart_at`` names bars handled by a brand-new watcher, the way a
    process restart leaves things: memory is gone, the paper book and the
    candle store are not. That walk runs in live mode so the watcher reads
    exits from the book.
    """
    paper = _CandlePaper(symbol)
    live_mode = restart_at is not None

    def _watcher() -> V1PaperWatcher:
        return V1PaperWatcher(
            enabled=True,
            replay_mode=not live_mode,
            emit_alerts=False,
            paper_engine=paper,
        )

    watcher = _watcher()
    skips: dict[str, str] = {}
    for i in range(STORE, len(h1)):
        bar = h1[i]
        paper.manage(bar)
        if live_mode and bar["time"].isoformat() in restart_at:
            watcher = _watcher()
        watcher.on_closed_1h(
            symbol,
            candles_1h=h1[i + 1 - STORE : i + 1],
            candles_4h=_closed_4h(h4, bar)[-STORE:],
            force=live_mode,
        )
        skip = watcher.status()["last_skip_by_symbol"].get(symbol) or {}
        if skip.get("bar") == bar["time"].isoformat():
            skips[skip["bar"]] = str(skip["reason"]).split(":", 1)[1]
    return paper.trades, skips


# --- look-ahead / repaint ----------------------------------------------------


def test_signal_at_bar_is_unchanged_by_later_candles(h1, h4):
    """A decision computed at T from data up to T survives appended future bars."""
    combo = get_combination("COMBO_02")
    assert combo is not None and combo.htf_require_fully_closed
    scfg, rcfg = SignalConfig(), ResearchConfig()
    fields = ("status", "direction", "entry_price", "stop_price", "tp1", "gates", "reason")
    candidates = 0
    for t in range(len(h1) - 330, len(h1) - 30):
        prefix = h1[t - 249 : t + 1]
        at_t = evaluate_combination_at_bar(
            symbol=SYMBOL,
            timeframe="1h",
            candles=prefix,
            as_of_index=len(prefix) - 1,
            combination=combo,
            signal_config=scfg,
            research_config=rcfg,
            candles_1h=prefix,
            candles_4h=_closed_4h(h4, h1[t]),
            compute_sd=False,
        )
        # Same window start, 30 more 1h bars and every later 4h bar appended.
        extended = h1[t - 249 : t + 31]
        later = evaluate_combination_at_bar(
            symbol=SYMBOL,
            timeframe="1h",
            candles=extended,
            as_of_index=249,
            combination=combo,
            signal_config=scfg,
            research_config=rcfg,
            candles_1h=extended,
            candles_4h=h4,
            compute_sd=False,
        )
        assert {k: at_t.get(k) for k in fields} == {k: later.get(k) for k in fields}, h1[t]["time"]
        assert (at_t.get("htf") or {}).get("trend_4h") == (later.get("htf") or {}).get("trend_4h")
        candidates += at_t.get("status") == "LONG_ENTRY_CANDIDATE"
    assert candidates > 0


def test_forming_4h_bar_is_never_read(h1, h4):
    """The 4h bar still forming at the 1h close cannot move the decision."""
    combo = get_combination("COMBO_02")
    scfg, rcfg = SignalConfig(), ResearchConfig()
    for t in range(len(h1) - 120, len(h1) - 8):
        window = h1[t - 249 : t + 1]
        closed = _closed_4h(h4, h1[t])
        forming = next((c for c in h4 if c["time"] == closed[-1]["time"] + H4), None)
        if forming is None or forming["time"] + H4 <= h1[t]["time"] + H1:
            continue
        spiked = {**forming, "high": forming["high"] * 2, "close": forming["high"] * 2}
        kwargs = dict(
            symbol=SYMBOL,
            timeframe="1h",
            candles=window,
            as_of_index=len(window) - 1,
            combination=combo,
            signal_config=scfg,
            research_config=rcfg,
            candles_1h=window,
            compute_sd=False,
        )
        base = evaluate_combination_at_bar(candles_4h=closed, **kwargs)
        with_forming = evaluate_combination_at_bar(candles_4h=closed + [spiked], **kwargs)
        assert base.get("status") == with_forming.get("status")
        assert base.get("htf") == with_forming.get("htf")


# --- data integrity gate -----------------------------------------------------


def test_integrity_passes_on_clean_closed_series(h1, h4):
    assert series_integrity_fault(h1, _closed_4h(h4, h1[-1])) is None


def test_integrity_faults(h1, h4):
    closed = _closed_4h(h4, h1[-1])
    assert series_integrity_fault(h1[:-5] + h1[-4:], closed) == "1h_gap"
    assert series_integrity_fault(h1, closed[:-3] + closed[-2:]) == "4h_gap"
    assert series_integrity_fault(h1[:-1] + [h1[-2]], closed) == "1h_out_of_order"
    assert series_integrity_fault(h1, closed[:-1]) == "4h_tip_behind"
    forming = {**closed[-1], "time": closed[-1]["time"] + H4}
    assert series_integrity_fault(h1, closed + [forming]) == "4h_tip_not_closed"
    rolled = {**h1[-1], "source": "binance_trade_roll"}
    assert series_integrity_fault(h1[:-1] + [rolled], closed) == "1h_tip_unconfirmed"
    skewed = {**h1[-1], "time": h1[-1]["time"] + timedelta(minutes=1)}
    assert series_integrity_fault(h1[:-1] + [skewed], closed) == "1h_misaligned"


def test_gap_outside_the_evaluated_window_is_ignored(h1, h4):
    old_gap = h1[:100] + h1[101:]
    assert series_integrity_fault(old_gap, _closed_4h(h4, h1[-1])) is None


def test_live_store_fault_blocks_without_watermark_then_retries(monkeypatch, h1, h4):
    """A 1h close handled before its 4h close waits; the bar is not consumed."""
    tip = len(h1) - 1
    while (h1[tip]["time"] + H1).hour % 4:
        tip -= 1
    series_1h = h1[tip + 1 - STORE : tip + 1]
    closed = _closed_4h(h4, h1[tip])
    store: dict[str, list[dict[str, Any]]] = {"1h": series_1h, "4h": closed[:-1]}
    monkeypatch.setattr(
        store_mod.ohlcv_store,
        "get_candles_for_engine",
        lambda _sym, tf, include_open=False: list(store[tf]),
    )
    watcher = V1PaperWatcher(
        enabled=True, replay_mode=True, emit_alerts=False, paper_engine=_NoPaper()
    )
    assert watcher.on_closed_1h(SYMBOL) is None
    skip = watcher.status()["last_skip_by_symbol"][SYMBOL]
    assert skip["reason"] == f"{SYMBOL}:data_integrity:4h_tip_behind"
    assert watcher.status()["processed_bars"] == 0
    assert SYMBOL not in watcher.status()["watermarks"]

    store["4h"] = closed
    watcher.on_closed_1h(SYMBOL)
    assert watcher.status()["processed_bars"] == 1
    assert "data_integrity" not in str(
        (watcher.status()["last_skip_by_symbol"].get(SYMBOL) or {}).get("reason")
    )


# --- batch backtest vs one closed candle at a time ---------------------------


@pytest.fixture(scope="module", params=["BTCUSDT", "SOLUSDT"])
def walk(request) -> SimpleNamespace:
    """Batch run and uninterrupted live walk over the same 1,500 closed 1h bars."""
    symbol = request.param
    name = symbol.lower() + "_{tf}_parity_2026-10-12.json"
    bars_1h = _load("1h", name)
    bars_4h = _load("4h", name)
    assert all(b["time"] - a["time"] == H1 for a, b in zip(bars_1h, bars_1h[1:]))
    assert all(b["time"] - a["time"] == H4 for a, b in zip(bars_4h, bars_4h[1:]))
    assert len(bars_1h) - STORE >= PARITY_BARS

    # The batch walk starts with no memory. That equals the live state only
    # when the level is not already broken on the bar before the first one.
    cfg = SignalConfig()
    swing_cfg = cfg.swing_for("1h")
    swings = detect_swings(
        bars_1h,
        left=swing_cfg.swing_left_bars,
        right=swing_cfg.swing_right_bars,
        symbol=symbol,
        timeframe="1h",
        atr_period=cfg.atr_period,
        minimum_swing_distance_atr=swing_cfg.minimum_swing_distance_atr,
        as_of_index=STORE - 1,
    )
    assert not _bos_break_possible(bars_1h, STORE - 1, swings, direction_filter="LONG")

    watcher_mod.reset_v1_paper_watcher_for_tests()
    live, skips = _live(bars_1h, bars_4h, symbol=symbol)
    return SimpleNamespace(
        symbol=symbol,
        h1=bars_1h,
        h4=bars_4h,
        batch=_batch(bars_1h, bars_4h, symbol=symbol),
        live=live,
        skips=skips,
    )


def _reentries(batch: dict[str, dict[str, Any]]) -> list[str]:
    """Trades that enter on the bar right after the previous trade's exit bar."""
    out: list[str] = []
    prev_exit: datetime | None = None
    for when in sorted(batch):
        if prev_exit is not None and datetime.fromisoformat(when) == prev_exit + H1:
            out.append(when)
        prev_exit = datetime.fromisoformat(batch[when]["exit_time"])
    return out


def test_live_walk_reproduces_batch_trades(walk):
    """Same bars, entry, stop and TP1, including exit-then-re-enter trades."""
    assert len(walk.batch) >= 6
    assert sorted(walk.live) == sorted(walk.batch)
    for when, trade in walk.batch.items():
        assert walk.live[when]["entry"] == pytest.approx(trade["entry_price"]), when
        assert walk.live[when]["stop"] == pytest.approx(trade["stop_price"]), when
        assert walk.live[when]["tp1"] == pytest.approx(trade["tp1"]), when
        assert walk.live[when]["exit_time"] == trade["exit_time"], when
    assert len(_reentries(walk.batch)) >= 2


def test_exit_bar_is_skipped_and_next_bar_may_reenter(walk):
    for trade in walk.batch.values():
        assert walk.skips[trade["exit_time"]] == "exit_bar_no_entry"
    for when in _reentries(walk.batch):
        # Same broken level, no new swing needed: the exit cleared the break.
        assert when in walk.live


def test_restart_takes_the_same_signals_as_the_uninterrupted_walk(walk):
    """Forced restarts add no signal and lose none."""
    times = [bar["time"] for bar in walk.h1[STORE:]]
    restart_at = {t.isoformat() for t in times[25::50]}
    for when, reason in walk.skips.items():
        # On a consumed break or an exit bar, and on the bar after it.
        if reason in ("sticky_bos_skip", "exit_bar_no_entry"):
            restart_at |= {when, (datetime.fromisoformat(when) + H1).isoformat()}
    for when in walk.live:
        # On the first bar spent in each trade.
        restart_at.add((datetime.fromisoformat(when) + H1).isoformat())
    assert len(restart_at) > 100
    restarted, _ = _live(
        walk.h1, walk.h4, symbol=walk.symbol, restart_at=frozenset(restart_at)
    )
    assert sorted(restarted) == sorted(walk.live)
    for when, trade in walk.live.items():
        for field in ("entry", "stop", "tp1", "exit_time"):
            assert restarted[when][field] == trade[field], (when, field)


def test_new_watcher_does_not_reopen_a_still_broken_level(h1):
    """Missing memory is not "no prior break"."""
    running = V1PaperWatcher(enabled=True, replay_mode=True, emit_alerts=False)
    checked = 0
    for i in range(STORE, len(h1)):
        window = h1[i + 1 - STORE : i + 1]
        allows = running._bos_break_allows_entry(SYMBOL, window, STORE - 1)
        broken = running._prev_break[SYMBOL]
        if allows or not broken:
            continue
        # Broken and already consumed. A watcher created now must agree.
        restarted = V1PaperWatcher(enabled=True, replay_mode=True, emit_alerts=False)
        assert restarted._bos_break_allows_entry(SYMBOL, window, STORE - 1) is False
        # The backtest's first bar has no memory; only an explicit reset does that.
        fresh = V1PaperWatcher(enabled=True, replay_mode=True, emit_alerts=False)
        assert fresh._bos_break_allows_entry(SYMBOL, window, STORE - 1, reset_state=True)
        checked += 1
    assert checked > 20


class _LedgerPaper:
    """No open position; one closed V1 row. Refuses every open."""

    def __init__(self, closed_at: datetime | None, *, open_now: bool = False) -> None:
        self.closed_at = closed_at
        self.open_now = open_now
        self.open_calls = 0

    def has_open(self, _symbol: str, stream: str) -> bool:
        return self.open_now and stream == "V1"

    def positions(self, *, closed_limit: int = 50) -> dict[str, Any]:
        if self.closed_at is None:
            return {"open": [], "closed": []}
        row = {
            "symbol": SYMBOL,
            "status": "CLOSED",
            "timeframe": "1h",
            "closed_at": self.closed_at.isoformat(),
            "signal_snippet": {"source": "V1_PAPER_WATCHER", "strategy_id": "COMBO_02_V1"},
        }
        return {"open": [], "closed": [row][:closed_limit]}

    def open_v1_combo_position(self, **_: Any) -> None:
        self.open_calls += 1
        return None


def _reason(watcher: V1PaperWatcher) -> str:
    skip = watcher.status()["last_skip_by_symbol"].get(SYMBOL) or {}
    return str(skip.get("reason") or "")


def test_exit_stamped_in_the_next_bar_before_this_bar_was_handled(h1, h4):
    """The exit bar is the bar the close fell in, whenever the watcher runs."""
    j = len(h1) - 10
    # Position was open through bar j and stopped out 40 s into bar j+1.
    paper = _LedgerPaper(h1[j + 1]["time"] + timedelta(seconds=40))
    watcher = V1PaperWatcher(
        enabled=True, replay_mode=False, emit_alerts=False, paper_engine=paper
    )

    def _handle(i: int) -> str:
        watcher.on_closed_1h(
            SYMBOL,
            candles_1h=h1[i + 1 - STORE : i + 1],
            candles_4h=_closed_4h(h4, h1[i])[-STORE:],
            force=True,
        )
        return _reason(watcher)

    assert _handle(j) == f"{SYMBOL}:exit_bar_no_entry"
    assert _handle(j + 1) == f"{SYMBOL}:exit_bar_no_entry"
    assert watcher._prev_break[SYMBOL] is False
    assert _handle(j + 2) != f"{SYMBOL}:exit_bar_no_entry"


def test_bar_with_an_open_position_is_not_evaluated_for_entry(h1, h4):
    paper = _LedgerPaper(None, open_now=True)
    watcher = V1PaperWatcher(
        enabled=True, replay_mode=False, emit_alerts=False, paper_engine=paper
    )

    def _handle(i: int) -> str:
        watcher.on_closed_1h(
            SYMBOL,
            candles_1h=h1[i + 1 - STORE : i + 1],
            candles_4h=_closed_4h(h4, h1[i])[-STORE:],
            force=True,
        )
        return _reason(watcher)

    for i in range(len(h1) - 40, len(h1) - 1):
        assert _handle(i) == f"{SYMBOL}:position_open"
    # Gone by the next close, with no ledger row to say so: that bar is the exit bar.
    paper.open_now = False
    assert _handle(len(h1) - 1) == f"{SYMBOL}:exit_bar_no_entry"
    assert watcher._prev_break[SYMBOL] is False
    assert paper.open_calls == 0


# --- TP1 swing lookback --------------------------------------------------------


def test_compute_targets_lookback_argument_defaults_to_all_swings():
    swings = [
        SimpleNamespace(swing_type="HIGH", price=103.0, bar_index=10),
        SimpleNamespace(swing_type="HIGH", price=106.0, bar_index=400),
    ]
    kwargs = dict(
        direction="LONG", entry_price=100.0, stop={"risk_per_unit": 1.0}, swings=swings
    )
    assert compute_targets(**kwargs)[0]["target_price"] == 103.0
    assert compute_targets(**kwargs, min_swing_bar_index=None)[0]["target_price"] == 103.0
    assert compute_targets(**kwargs, min_swing_bar_index=300)[0]["target_price"] == 106.0
    # No swing high left at 2R or more: TP1 is the 2R fallback.
    fallback = compute_targets(**kwargs, min_swing_bar_index=401)[0]
    assert fallback["target_type"] == "r_multiple"
    assert fallback["target_price"] == pytest.approx(102.0)


def test_tp1_swing_cap_applies_to_combo02_1h_long_only(monkeypatch, h1, h4):
    assert engine_mod.COMBO02_TP1_SWING_LOOKBACK_1H == watcher_mod._MAX_EVAL_BARS_1H
    seen: list[int | None] = []
    real = engine_mod.compute_targets

    def _spy(**kwargs: Any):
        seen.append(kwargs.get("min_swing_bar_index"))
        return real(**kwargs)

    monkeypatch.setattr(engine_mod, "compute_targets", _spy)
    assert _batch(h1, h4)
    assert seen and all(cap is not None for cap in seen)

    # Any other combination keeps every swing.
    seen.clear()
    _batch(h1, h4, combination="COMBO_02_LOCAL")
    assert seen and all(cap is None for cap in seen)

    # The cap is the live window: a swing needs its left bars inside it.
    seen.clear()
    combo = get_combination("COMBO_02")
    when = sorted(_batch(h1, h4))[0]
    idx = next(i for i, c in enumerate(h1) if c["time"].isoformat() == when)
    evaluate_combination_at_bar(
        symbol=SYMBOL,
        timeframe="1h",
        candles=h1,
        as_of_index=idx,
        combination=combo,
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        candles_1h=h1,
        candles_4h=_closed_4h(h4, h1[idx]),
        compute_sd=False,
    )
    left = SignalConfig().swing_for("1h").swing_left_bars
    assert seen[-1] == idx - (watcher_mod._MAX_EVAL_BARS_1H - 1) + left
