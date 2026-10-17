"""Per-coin allowlist of the passed book: loader, paper watcher, regime book, parity.

A coin with an entry trades only the passed sides listed for it; a signal
from any other passed side on that coin is skipped (paper
``symbol_side_not_allowed``, backtest ``SYMBOL_SIDE_NOT_ALLOWED``). A coin
with no entry trades every passed side.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import app.services.passed_paper_watcher as watcher_mod
from app.research import passed_regime_backtest as book
from app.research import passed_symbol_allowlist as allowlist_mod
from app.research import regime_backtest_job as job_mod
from app.research.passed_cap_book import PASSED_SIDES
from app.research.passed_symbol_allowlist import (
    ALLOWLIST_PATH,
    AllowlistError,
    allowed_sides_for,
    load_allowlist,
    parse_allowlist,
)
from app.services.passed_paper_watcher import PassedPaperWatcher

H = timedelta(hours=1)
START = datetime(2026, 1, 1, tzinfo=timezone.utc)  # midnight: 4h-aligned
N_BARS = 280

SYM = "BTCUSDT"  # has an entry
OTHER = "ETHUSDT"  # no entry

A = ("COMBO_01", "1h", "SHORT")  # allowed on SYM
B = ("COMBO_02", "1h", "SHORT")  # failed SYM's test
C = ("COMBO_02_V2", "1h", "SHORT")  # allowed on SYM

TESTED = {
    "window": {"start": "2025-01-01", "end": "2025-12-31"},
    "pass_rule": "net > 0 and trades >= 5",
    "pnl_model": "CAUSAL_NEXT_OPEN",
    "decided_at": "2026-10-17T00:00:00+00:00",
    "results": [
        {"strategy_id": "COMBO_01", "direction": "SHORT", "timeframe": "1h", "trades": 9, "net_usd": 12.5, "pass": True},
        {"strategy_id": "COMBO_02", "direction": "SHORT", "timeframe": "1h", "trades": 7, "net_usd": -4.0, "pass": False},
    ],
}


def _entry(*sides: tuple[str, str, str]) -> dict[str, Any]:
    return {
        "allowed_sides": [{"strategy_id": c, "direction": d, "timeframe": t} for c, t, d in sides],
        "tested": json.loads(json.dumps(TESTED)),
    }


def _allowlist(symbol: str = SYM, sides=(A, C)):
    return parse_allowlist({"version": 1, "symbols": {symbol: _entry(*sides)}}, source="test")


# --- loader -------------------------------------------------------------------


def test_committed_file_loads_and_validates():
    loaded = load_allowlist()
    assert loaded.version == 1
    assert loaded.source == ALLOWLIST_PATH.name
    for sym, entry in loaded.entries.items():
        assert set(entry.allowed) <= set(PASSED_SIDES), sym


def test_empty_file_means_every_side_everywhere(tmp_path):
    path = tmp_path / "allow.json"
    path.write_text(json.dumps({"version": 1, "symbols": {}}), encoding="utf-8")
    loaded = load_allowlist(path)
    assert loaded.entries == {}
    assert loaded.allowed_sides_for(SYM) is None
    assert loaded.is_allowed(SYM, B)
    assert loaded.describe() == {"version": 1, "source": "allow.json", "symbols": {}}


def test_valid_entry_lists_allowed_and_blocked_in_book_order(tmp_path):
    path = tmp_path / "allow.json"
    # Listed out of book order, direction in lower case: normalised on load.
    raw = _entry(C, A)
    raw["allowed_sides"][0]["direction"] = "short"
    path.write_text(json.dumps({"version": 1, "symbols": {SYM: raw}}), encoding="utf-8")
    loaded = load_allowlist(path)
    assert loaded.allowed_sides_for(SYM) == frozenset({A, C})
    assert loaded.allowed_sides_for("btcusdt") == frozenset({A, C})
    assert loaded.allowed_sides_for(OTHER) is None
    entry = loaded.entry(SYM)
    assert entry.allowed == tuple(s for s in PASSED_SIDES if s in {A, C})
    assert entry.blocked == tuple(s for s in PASSED_SIDES if s not in {A, C})
    view = loaded.describe()["symbols"][SYM]
    assert [r["strategy_id"] for r in view["allowed_sides"]] == ["COMBO_02_V2", "COMBO_01"]
    assert B[0] in [r["strategy_id"] for r in view["blocked_sides"]]
    assert view["tested_window"] == {"start": "2025-01-01", "end": "2025-12-31"}
    assert view["decided_at"] == TESTED["decided_at"]
    assert view["pass_rule"] == "net > 0 and trades >= 5"
    assert view["results"] == TESTED["results"]


def test_empty_allowed_sides_blocks_every_side():
    loaded = _allowlist(SYM, ())
    assert loaded.allowed_sides_for(SYM) == frozenset()
    assert not loaded.is_allowed(SYM, A)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d["symbols"][SYM]["allowed_sides"].append({"strategy_id": "COMBO_SD_F", "direction": "SHORT", "timeframe": "1h"}), "not a PASSED_SIDES row"),
        (lambda d: d["symbols"][SYM]["allowed_sides"].append({"strategy_id": "COMBO_01", "direction": "LONG", "timeframe": "1h"}), "not a PASSED_SIDES row"),
        (lambda d: d["symbols"][SYM]["allowed_sides"].append({"strategy_id": "COMBO_01", "direction": "SIDEWAYS", "timeframe": "1h"}), "LONG or SHORT"),
        (lambda d: d["symbols"][SYM]["allowed_sides"].append({"strategy_id": "COMBO_01", "direction": "SHORT", "timeframe": "4h"}), "not a PASSED_SIDES row"),
        (lambda d: d["symbols"][SYM]["allowed_sides"].append(dict(d["symbols"][SYM]["allowed_sides"][0])), "twice"),
        (lambda d: d["symbols"].update({"WIFUSDT": d["symbols"][SYM]}), "cap-book"),
        (lambda d: d["symbols"].update({"btcusdt": d["symbols"].pop(SYM)}), "upper case"),
        (lambda d: d["symbols"][SYM].pop("allowed_sides"), "allowed_sides"),
        (lambda d: d["symbols"][SYM].pop("tested"), "tested"),
        (lambda d: d["symbols"][SYM]["tested"].pop("decided_at"), "decided_at"),
        (lambda d: d["symbols"][SYM]["tested"]["window"].pop("end"), "window"),
        (lambda d: d.update({"version": 2}), "version"),
        (lambda d: d.update({"symbols": []}), "symbols"),
    ],
)
def test_invalid_file_is_rejected_at_load(tmp_path, mutate, match):
    data = {"version": 1, "symbols": {SYM: _entry(A)}}
    mutate(data)
    path = tmp_path / "allow.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(AllowlistError, match=match):
        load_allowlist(path)


def test_unreadable_file_is_rejected(tmp_path):
    with pytest.raises(AllowlistError, match="not found"):
        load_allowlist(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(AllowlistError, match="cannot read"):
        load_allowlist(bad)


def test_module_allowed_sides_for_reads_the_process_allowlist():
    # conftest pins an empty allowlist for every test.
    assert allowed_sides_for(SYM) is None
    allowlist_mod.set_allowlist_for_tests(_allowlist())
    assert allowed_sides_for(SYM) == frozenset({A, C})
    assert allowed_sides_for(OTHER) is None


def test_get_allowlist_loads_the_committed_file_once(monkeypatch):
    allowlist_mod.set_allowlist_for_tests(None)
    calls: list[int] = []
    real = allowlist_mod.load_allowlist

    def counting(path=None):
        calls.append(1)
        return real(path)

    monkeypatch.setattr(allowlist_mod, "load_allowlist", counting)
    first = allowlist_mod.get_allowlist()
    assert allowlist_mod.get_allowlist() is first
    assert calls == [1]


# --- synthetic bars and signals shared by paper and the regime book ------------


def _bars(n: int = N_BARS) -> list[dict]:
    return [
        {"time": START + i * H, "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1.0}
        for i in range(n)
    ]


def _h4_for(tip_time: datetime) -> list[dict]:
    """80 closed 4h bars ending at the last one closed at the 1h tip's close."""
    decision = int(tip_time.timestamp()) + 3600
    last = datetime.fromtimestamp((decision // 14400) * 14400 - 14400, tz=timezone.utc)
    return [
        {"time": last - (79 - i) * 4 * H, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}
        for i in range(80)
    ]


# side -> [(signal bar, exit bar)], the same on both coins.
PLAN: dict[tuple[str, str, str], list[tuple[int, int]]] = {
    B: [(260, 262), (266, 267), (272, 274)],
    A: [(264, 268), (272, 274)],
    C: [(266, 267), (270, 271)],
}


class FakeWalk:
    """run_combination_backtest stand-in. Skips the side's own open-trade bars, like the real walk."""

    def __init__(self, plan=PLAN) -> None:
        self.plan = plan
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        candles = kw["candles"]
        lo = kw["index_start"] or 0
        hi = kw["index_end"] if kw["index_end"] is not None else len(candles)
        side = (kw["combination"], kw["timeframe"], kw["direction_filter"])
        trades, free_from = [], lo
        for bar, exit_bar in self.plan.get(side, []):
            if bar < free_from or bar >= hi:
                continue
            closed = exit_bar < hi
            trades.append(
                {
                    "symbol": kw["symbol"],
                    "timeframe": kw["timeframe"],
                    "combination_id": kw["combination"],
                    "entry_index": bar,
                    "signal_time": candles[bar]["time"].isoformat(),
                    "direction": kw["direction_filter"],
                    "entry_price": 100.0,
                    "stop_price": 102.0,
                    "tp1": 96.0,
                    "exit_index": exit_bar if closed else None,
                    "exit_time": candles[exit_bar]["time"].isoformat() if closed else None,
                    "exit_price": 102.0 if closed else None,
                    "outcome": "SL" if closed else "OPEN",
                }
            )
            free_from = exit_bar + 1 if closed else hi
        return {"status": "OK", "trades": trades}


def _bearish(monkeypatch) -> None:
    def fake(candles, signal_config):
        last = candles[-1]["time"] if candles else None
        return {"trend": "BEARISH", "as_of_bar": last.isoformat() if last else "", "bars": len(candles)}

    monkeypatch.setattr(book, "daily_trend", fake)


def _daily() -> list[dict]:
    return [
        {"time": START - timedelta(days=60) + timedelta(days=i), "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}
        for i in range(70)
    ]


def _backtest_decisions(symbol: str, monkeypatch, **kwargs) -> tuple[dict, dict]:
    _bearish(monkeypatch)
    out = book.run_symbol(
        symbol,
        {"candles": _bars(), "eval_start": 250},
        book.RegimeClock(_daily(), None),
        backtest_fn=FakeWalk(),
        pnl_model=book.LEGACY_SIGNAL_CLOSE,
        **kwargs,
    )
    decisions = {}
    for s in out["signals"]:
        decision = "taken" if s["taken"] else {
            book.SKIP_SYMBOL_SIDE_NOT_ALLOWED: "not_allowed",
            book.SKIP_POSITION_OPEN: "position",
            book.SKIP_SAME_BAR: "position",
        }[s["skip_reason"]]
        decisions[(s["bar_index"], s["strategy_id"], s["direction"])] = decision
    return decisions, out


class FakePaper:
    """Paper engine stand-in: a position holds the coin through its plan exit bar."""

    def __init__(self) -> None:
        self.now = 0
        self.held: dict[str, int] = {}
        self.opened: list[dict[str, Any]] = []

    def open_symbols(self) -> set[str]:
        return {sym for sym, until in self.held.items() if self.now <= until}

    def open_passed_position(self, **kw: Any) -> Any:
        side = (kw["combination_id"], kw["timeframe"], kw["direction"])
        exit_bar = dict(PLAN[side])[self.now]
        self.held[kw["symbol"]] = exit_bar
        self.opened.append({"bar": self.now, **kw})
        return SimpleNamespace(id=f"p{len(self.opened)}")

    def status(self) -> dict[str, Any]:
        return {}


def _paper_decisions(symbol: str, monkeypatch, watcher: PassedPaperWatcher, paper: FakePaper) -> dict:
    signals = {(bar, side[0]) for side, rows in PLAN.items() for bar, _ in rows}

    def evaluate(**kw):
        hit = (kw["as_of_index"], kw["combination"].combination_id) in signals
        return {"status": "SHORT_ENTRY_CANDIDATE" if hit else "NO_SETUP", "entry_price": 100.0, "stop_price": 102.0, "tp1": 96.0}

    monkeypatch.setattr(watcher_mod, "evaluate_combination_at_bar", evaluate)
    bars = _bars()
    for k in range(250, N_BARS - 1):
        paper.now = k
        watcher.on_closed_1h(
            symbol,
            candles_1h=bars[: k + 1],
            candles_4h=_h4_for(bars[k]["time"]),
            candles_15m=bars[: k + 1],
            market_direction="BEARISH",
            force=True,
            next_bar=bars[k + 1],
        )
    index = {bar["time"].isoformat(): i for i, bar in enumerate(bars)}
    mapping = {"": "taken", "symbol_side_not_allowed": "not_allowed", "symbol_has_open_position": "position"}
    return {
        (index[row["setup_bar_time"]], row["strategy_id"], row["direction"]): mapping[row["reason"]]
        for row in watcher.regime_status()["latest_signals"]
        if row["symbol"] == symbol
    }


EXPECTED_SYM = {
    (260, "COMBO_02", "SHORT"): "not_allowed",
    (264, "COMBO_01", "SHORT"): "taken",
    (266, "COMBO_02_V2", "SHORT"): "position",
    (266, "COMBO_02", "SHORT"): "not_allowed",  # checked before the open position
    (270, "COMBO_02_V2", "SHORT"): "taken",
    (272, "COMBO_02", "SHORT"): "not_allowed",
    (272, "COMBO_01", "SHORT"): "taken",
}
EXPECTED_OTHER = {
    (260, "COMBO_02", "SHORT"): "taken",
    (264, "COMBO_01", "SHORT"): "taken",
    (266, "COMBO_02_V2", "SHORT"): "position",
    (266, "COMBO_02", "SHORT"): "position",
    (270, "COMBO_02_V2", "SHORT"): "taken",
    (272, "COMBO_02", "SHORT"): "taken",  # book order: COMBO_02 before COMBO_01
    (272, "COMBO_01", "SHORT"): "position",
}


# --- paper watcher ------------------------------------------------------------


@pytest.mark.usefixtures("passed_book_open")
def test_paper_blocks_failed_side_on_the_coin_and_opens_the_allowed_one(monkeypatch):
    paper = FakePaper()
    watcher = PassedPaperWatcher(enabled=True, paper_engine=paper, symbol_allowlist=_allowlist())
    decisions = _paper_decisions(SYM, monkeypatch, watcher, paper)
    assert decisions == EXPECTED_SYM
    opened = [(row["bar"], row["combination_id"]) for row in paper.opened]
    assert opened == [(264, "COMBO_01"), (270, "COMBO_02_V2"), (272, "COMBO_01")]
    assert all(row["combination_id"] != B[0] for row in paper.opened)


@pytest.mark.usefixtures("passed_book_open")
def test_paper_records_the_skip_reason_and_last_skip(monkeypatch):
    paper = FakePaper()
    watcher = PassedPaperWatcher(enabled=True, paper_engine=paper, symbol_allowlist=_allowlist())
    signals = {(260, B[0])}

    def evaluate(**kw):
        hit = (kw["as_of_index"], kw["combination"].combination_id) in signals
        return {"status": "SHORT_ENTRY_CANDIDATE" if hit else "NO_SETUP", "entry_price": 100.0, "stop_price": 102.0, "tp1": 96.0}

    monkeypatch.setattr(watcher_mod, "evaluate_combination_at_bar", evaluate)
    bars = _bars()
    paper.now = 260
    opened = watcher.on_closed_1h(
        SYM,
        candles_1h=bars[:261],
        candles_4h=_h4_for(bars[260]["time"]),
        candles_15m=bars[:261],
        market_direction="BEARISH",
        force=True,
        next_bar=bars[261],
    )
    assert opened == [] and paper.opened == []
    status = watcher.regime_status()
    row = status["latest_signals"][0]
    assert (row["strategy_id"], row["outcome"], row["reason"]) == ("COMBO_02", "not_opened", "symbol_side_not_allowed")
    assert status["last_skip"] == f"{SYM}:symbol_side_not_allowed"
    assert status["last_evaluated_bar"]["signals_not_allowed"] == 1


@pytest.mark.usefixtures("passed_book_open")
def test_paper_coin_without_entry_is_unaffected(monkeypatch):
    paper = FakePaper()
    watcher = PassedPaperWatcher(enabled=True, paper_engine=paper, symbol_allowlist=_allowlist())
    assert _paper_decisions(OTHER, monkeypatch, watcher, paper) == EXPECTED_OTHER
    assert paper.opened[0]["combination_id"] == B[0]


def test_paper_status_exposes_the_allowlist():
    watcher = PassedPaperWatcher(enabled=True, paper_engine=FakePaper(), symbol_allowlist=_allowlist())
    watcher.last_regime = {"ok": True, "trend": "BEARISH", "bar": "2026-01-01T00:00:00+00:00"}
    view = watcher.regime_status()["symbol_allowlist"]
    assert set(view["symbols"]) == {SYM}
    entry = view["symbols"][SYM]
    assert {r["strategy_id"] for r in entry["allowed_sides"]} == {A[0], C[0]}
    assert {"strategy_id": B[0], "timeframe": "1h", "direction": "SHORT"} in entry["blocked_sides"]
    assert entry["tested_window"] == TESTED["window"]
    assert entry["decided_at"] == TESTED["decided_at"]


def test_paper_watcher_uses_the_process_allowlist_by_default():
    allowlist_mod.set_allowlist_for_tests(_allowlist())
    watcher = PassedPaperWatcher(enabled=True, paper_engine=FakePaper())
    assert watcher.symbol_allowlist.allowed_sides_for(SYM) == frozenset({A, C})


def test_paper_watcher_refuses_a_bad_file(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": 1, "symbols": {SYM: _entry(("COMBO_SD_F", "1h", "SHORT"))}}))
    monkeypatch.setattr(allowlist_mod, "ALLOWLIST_PATH", bad)
    allowlist_mod.set_allowlist_for_tests(None)
    with pytest.raises(AllowlistError):
        PassedPaperWatcher(enabled=True, paper_engine=FakePaper())


# --- regime book ----------------------------------------------------------------


def test_regime_book_blocks_failed_side_on_the_coin(monkeypatch):
    decisions, out = _backtest_decisions(SYM, monkeypatch, symbol_allowlist=_allowlist())
    assert decisions == EXPECTED_SYM
    assert [(t["entry_index"], t["strategy_id"]) for t in out["trades"]] == [
        (264, "COMBO_01"),
        (270, "COMBO_02_V2"),
        (272, "COMBO_01"),
    ]
    view = out["symbol_allowlist"]
    assert view["applied"] is True
    assert {r["strategy_id"] for r in view["filter"]["allowed_sides"]} == {A[0], C[0]}


def test_regime_book_coin_without_entry_is_unaffected(monkeypatch):
    decisions, out = _backtest_decisions(OTHER, monkeypatch, symbol_allowlist=_allowlist())
    assert decisions == EXPECTED_OTHER
    assert out["symbol_allowlist"] == {"applied": True, "filter": None}


def test_regime_book_opt_out_runs_every_side(monkeypatch):
    decisions, out = _backtest_decisions(
        SYM, monkeypatch, symbol_allowlist=_allowlist(), apply_symbol_allowlist=False
    )
    assert decisions == EXPECTED_OTHER
    assert out["symbol_allowlist"] == {"applied": False, "filter": None}


def test_regime_book_reads_the_process_allowlist_by_default(monkeypatch):
    allowlist_mod.set_allowlist_for_tests(_allowlist())
    decisions, _ = _backtest_decisions(SYM, monkeypatch)
    assert decisions == EXPECTED_SYM


def test_run_regime_book_counts_the_skip_and_reports_the_filters(monkeypatch):
    _bearish(monkeypatch)

    def post(data):
        trade = dict(data["backtest_out"]["trades"][0])
        return {"trades": [{**trade, "gross_pnl_usd": -1.0, "fee_total_usd": 0.0, "net_pnl_usd": -1.0}]}

    books = {s: {"candles": _bars(), "eval_start": 250} for s in (SYM, OTHER)}
    out = book.run_regime_book(
        books,
        _daily(),
        backtest_fn=FakeWalk(),
        postprocess_fn=post,
        pnl_model=book.LEGACY_SIGNAL_CLOSE,
        symbol_allowlist=_allowlist(),
    )
    assert out["summary"]["skipped_signals"]["SYMBOL_SIDE_NOT_ALLOWED"] == 3
    assert out["symbol_allowlist"]["applied"] is True
    assert set(out["symbol_allowlist"]["symbols"]) == {SYM}
    assert out["symbols"][SYM]["symbol_allowlist"]["filter"]["decided_at"] == TESTED["decided_at"]
    assert out["symbols"][OTHER]["symbol_allowlist"]["filter"] is None
    off = book.run_regime_book(
        books,
        _daily(),
        backtest_fn=FakeWalk(),
        postprocess_fn=post,
        pnl_model=book.LEGACY_SIGNAL_CLOSE,
        symbol_allowlist=_allowlist(),
        apply_symbol_allowlist=False,
    )
    assert "SYMBOL_SIDE_NOT_ALLOWED" not in off["summary"]["skipped_signals"]
    assert off["symbol_allowlist"] == {"applied": False, "symbols": {}}


def test_scope_limits_the_reported_filter(monkeypatch):
    _, out = _backtest_decisions(SYM, monkeypatch, symbol_allowlist=_allowlist(), directions=["LONG"])
    view = out["symbol_allowlist"]["filter"]
    assert view["allowed_sides"] == []
    assert [r["direction"] for r in view["blocked_sides"]] == ["LONG"]


# --- parity -----------------------------------------------------------------------


@pytest.mark.usefixtures("passed_book_open")
@pytest.mark.parametrize("symbol", [SYM, OTHER])
def test_paper_and_regime_book_make_the_same_decisions(monkeypatch, symbol):
    allowlist = _allowlist()
    paper = FakePaper()
    watcher = PassedPaperWatcher(enabled=True, paper_engine=paper, symbol_allowlist=allowlist)
    on_paper = _paper_decisions(symbol, monkeypatch, watcher, paper)
    in_book, _ = _backtest_decisions(symbol, monkeypatch, symbol_allowlist=allowlist)
    assert on_paper == in_book


# --- job and route --------------------------------------------------------------


def test_job_child_step_carries_the_flag_and_the_allowlist(monkeypatch):
    _bearish(monkeypatch)
    payload = {
        "op": "symbol",
        "symbol": SYM,
        "book": {"candles": _bars(), "eval_start": 250},
        "btc_daily": _daily(),
        "pnl_model": book.LEGACY_SIGNAL_CLOSE,
        "symbol_allowlist": _allowlist(),
    }
    on = job_mod._execute(payload, backtest_fn=FakeWalk())
    assert any(s["skip_reason"] == "SYMBOL_SIDE_NOT_ALLOWED" for s in on["signals"])
    off = job_mod._execute({**payload, "apply_symbol_allowlist": False}, backtest_fn=FakeWalk())
    assert not any(s["skip_reason"] == "SYMBOL_SIDE_NOT_ALLOWED" for s in off["signals"])


def test_allowlist_pickles_for_the_worker():
    import pickle

    loaded = _allowlist()
    again = pickle.loads(pickle.dumps(loaded))
    assert again.allowed_sides_for(SYM) == loaded.allowed_sides_for(SYM)


@pytest.fixture
def fresh_job(monkeypatch):
    monkeypatch.setattr(job_mod, "_state", job_mod._idle_state())
    monkeypatch.setattr(job_mod, "_task", None)
    monkeypatch.setattr(job_mod, "_lock", asyncio.Lock())
    seen: list[dict] = []

    async def fake_run(job):
        seen.append(job)

    monkeypatch.setattr(job_mod, "_run", fake_run)
    return seen


def _start(**kw):
    async def go():
        out = await job_mod.start([SYM, OTHER], 200, **kw)
        await asyncio.sleep(0)
        return out

    return asyncio.run(go())


def test_job_start_reports_the_filters_it_applies(fresh_job):
    allowlist_mod.set_allowlist_for_tests(_allowlist())
    out = _start()
    assert out["status"] == "STARTED"
    job = out["job"]
    assert job["apply_symbol_allowlist"] is True
    assert set(job["symbol_allowlist"]["symbols"]) == {SYM}
    assert job["symbol_allowlist"]["applied"] is True
    assert "_symbol_allowlist" not in job
    assert fresh_job[0]["_symbol_allowlist"].allowed_sides_for(SYM) == frozenset({A, C})


def test_job_start_opt_out(fresh_job):
    allowlist_mod.set_allowlist_for_tests(_allowlist())
    out = _start(apply_symbol_allowlist=False)
    assert out["job"]["apply_symbol_allowlist"] is False
    assert out["job"]["symbol_allowlist"] == {"applied": False, "symbols": {}}
    assert fresh_job[0]["_symbol_allowlist"] is None


def test_job_start_rejects_a_bad_allowlist(fresh_job, monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": 1, "symbols": {SYM: _entry(("NOPE", "1h", "SHORT"))}}))
    monkeypatch.setattr(allowlist_mod, "ALLOWLIST_PATH", bad)
    allowlist_mod.set_allowlist_for_tests(None)
    out = _start()
    assert out["status"] == "REJECTED"
    assert "allowlist" in out["error"]
    assert fresh_job == []


def test_slim_result_keeps_the_filters():
    view = {"applied": True, "symbols": {SYM: {"allowed_sides": []}}}
    slim = job_mod.slim_result({"summary": {"by_strategy": {}}, "trades": [], "symbol_allowlist": view})
    assert slim["symbol_allowlist"] == view


@pytest.mark.parametrize(
    "body, expected",
    [({}, True), ({"apply_symbol_allowlist": False}, False), ({"apply_symbol_allowlist": "false"}, False), ({"apply_symbol_allowlist": True}, True)],
)
def test_route_passes_apply_symbol_allowlist(monkeypatch, body, expected):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import routes

    calls: list[dict] = []

    async def fake_start(symbols, limit=None, **kw):
        calls.append(kw)
        return {"status": "STARTED"}

    monkeypatch.setattr(job_mod, "start", fake_start)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    client = TestClient(app)
    client.post("/api/research/regime-backtest/start", json={"symbols": [SYM], "limit": 100, **body})
    assert calls[-1]["apply_symbol_allowlist"] is expected


def test_route_rejects_a_garbled_flag(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import routes

    async def fake_start(*a, **kw):
        raise AssertionError("must not start")

    monkeypatch.setattr(job_mod, "start", fake_start)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    out = TestClient(app).post(
        "/api/research/regime-backtest/start", json={"symbols": [SYM], "apply_symbol_allowlist": "maybe"}
    ).json()
    assert out["status"] == "REJECTED"
