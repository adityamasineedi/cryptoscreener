"""Approved trade-logic fixes (audit 2026-10-17 findings #8, #7, #9, #2).

1. Fee helper: gross PnL from the slipped entry fill the fee uses.
2. COMBO_02_V2 family: a LONG or SHORT request walks that side only;
   V2.1-A and a "BOTH" request walk both sides.
3. Causal book: one position at a time; overlapping fills are skipped and recorded.
4. Causal book: a missing bar inside a trade closes it as GAP_EXIT at the next
   printed open and holds the position until then.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.research.backtest_ui_config import (
    BacktestConfigError,
    TWO_SIDED_DIRECTION,
    v2_family_direction_filter,
    validate_backtest_request,
)
from app.research.causal_execution import (
    OUTCOME_GAP_EXIT,
    SKIP_FILL_BAR_GAP,
    SKIP_POSITION_OPEN,
    apply_causal_primary_display,
    build_execution_run_record,
    clear_isolated_execution_runs,
    compare_execution_models,
    execute_causal_trade,
)
from app.research.config import ResearchConfig
from app.research.trade_fees import enrich_trade_execution, enrich_trades

SIZING = dict(risk_usd=20.0, taker_fee=0.0004, maker_fee=0.0002, leverage=2.0, account_equity=1000.0)
T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
SLIP = 0.0005


# ---------------------------------------------------------------- fix 1: fee helper


@pytest.mark.parametrize(
    "direction, entry, stop, exit_",
    [("LONG", 100.0, 98.0, 104.0), ("SHORT", 100.0, 102.0, 96.0), ("LONG", 100.0, 98.0, 98.0)],
)
def test_gross_uses_the_slipped_entry_the_fee_uses(direction, entry, stop, exit_):
    slip = 0.0002
    trade = {"direction": direction, "entry_price": entry, "stop_price": stop, "exit_price": exit_, "outcome": "TP1"}
    row = enrich_trade_execution(trade, slippage_rate=slip, **SIZING)
    qty = row["qty"]
    if direction == "LONG":
        fill_in, fill_out = entry * (1 + slip), exit_ * (1 - slip)
        gross = qty * (fill_out - fill_in)
    else:
        fill_in, fill_out = entry * (1 - slip), exit_ * (1 + slip)
        gross = qty * (fill_in - fill_out)
    # Fees are charged on the same slipped fills (rates unchanged).
    assert row["fee_entry_usd"] == pytest.approx(-qty * fill_in * 0.0004, abs=1e-8)
    assert row["fee_exit_usd"] == pytest.approx(-qty * fill_out * 0.0004, abs=1e-8)
    assert row["notional_entry_usd"] == pytest.approx(qty * fill_in)
    assert row["gross_pnl_usd"] == pytest.approx(gross)
    assert row["net_pnl_usd"] == pytest.approx(gross + row["fee_total_usd"])
    # Size and risk stay on the planned entry.
    assert qty == pytest.approx(20.0 / abs(entry - stop))
    assert row["risk_usd"] == pytest.approx(20.0)
    assert row["r_net"] == pytest.approx(row["net_pnl_usd"] / row["risk_usd"])


def test_zero_slippage_is_unchanged():
    trade = {"direction": "LONG", "entry_price": 100.0, "stop_price": 98.0, "exit_price": 104.0, "outcome": "TP1"}
    row = enrich_trade_execution(trade, **SIZING)
    assert row["gross_pnl_usd"] == pytest.approx(10.0 * 4.0)
    assert row["net_pnl_usd"] == pytest.approx(40.0 - 10.0 * 100.0 * 0.0004 - 10.0 * 104.0 * 0.0004)


def test_audit_known_case_no_longer_overstated():
    # Audit A known case: slip 0.0002, qty 10, the old gross was 39.792 (0.2 too high).
    trade = {"direction": "LONG", "entry_price": 100.0, "stop_price": 98.0, "exit_price": 104.0, "outcome": "TP1"}
    row = enrich_trades([trade], slippage_rate=0.0002, **SIZING)[0]
    assert row["qty"] == pytest.approx(10.0)
    assert row["gross_pnl_usd"] == pytest.approx(39.592)


# ---------------------------------------------------------------- fix 2: V2 family direction


@pytest.mark.parametrize(
    "combo, direction, expected",
    [
        ("COMBO_02_V2", "LONG", "LONG"),
        ("COMBO_02_V2", "SHORT", "SHORT"),
        ("COMBO_02_V2", "BOTH", None),
        ("COMBO_02_V2_1B", "SHORT", "SHORT"),
        ("COMBO_02_V2_1B", "both", None),
        # V2.1-A is the hedge book's native two-sided run; it posts LONG.
        ("COMBO_02_V2_1_A", "LONG", None),
        ("COMBO_02_V2_1A", "SHORT", None),
    ],
)
def test_v2_family_direction_filter(combo, direction, expected):
    assert v2_family_direction_filter(combo, direction) == expected


def _validate(**kw):
    base = dict(symbols=["BTCUSDT"], timeframes=["1h"], allow_short=True, research_risk_override=True, risk_usd=20.0)
    base.update(kw)
    return validate_backtest_request(**base)


def test_both_is_accepted_for_the_v2_family_only():
    r = _validate(direction="BOTH", combination_id="COMBO_02_V2", strategy_id="COMBO_02_V2")
    assert r.direction == TWO_SIDED_DIRECTION
    assert r.production_comparable is False
    with pytest.raises(BacktestConfigError) as ei:
        _validate(direction="BOTH", combination_id="COMBO_02")
    assert ei.value.code == "ambiguous_direction"
    with pytest.raises(BacktestConfigError) as ei:
        _validate(direction="BOTH", combination_id="COMBO_02_V2", strategy_id="COMBO_02_V2", allow_short=False)
    assert ei.value.code == "short_research_paused"


def _flat_candles(n: int = 400) -> list[dict]:
    return [
        {"time": T0 + timedelta(hours=i), "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1.0}
        for i in range(n)
    ]


def _matrix_direction_filter(monkeypatch, combination_id: str, direction: str) -> tuple[object, dict]:
    import app.research.backtest_cpu_pool as pool
    import app.research.market_structure as ms
    import app.research.service as service

    seen: dict = {}

    async def fake_load(symbol, timeframe, **kwargs):
        return _flat_candles(), 0, {"symbol": symbol, "timeframe": timeframe}

    async def fake_htf(symbol, **kwargs):
        return None, None

    async def fake_walk(**kwargs):
        seen.update(kwargs)
        return {"trades": [], "result": {}, "status": "SUCCESS_EMPTY", "sample_size": 0}

    async def fake_post(data, **kwargs):
        return {"combination_id": data["combination_id"], "direction": data["direction"], "trades": []}

    monkeypatch.setattr(service, "_load_research_candles", fake_load)
    monkeypatch.setattr(service, "_load_htf_candles_for_combo", fake_htf)
    monkeypatch.setattr(pool, "run_combination_backtest_isolated", fake_walk)
    monkeypatch.setattr(pool, "run_matrix_postprocess_isolated", fake_post)
    monkeypatch.setattr(ms, "resolve_analytics_flags", lambda settings: (False, False))
    out = asyncio.run(
        service.BosResearchService().strategy_matrix(
            combination_id=combination_id, symbols=["BTCUSDT"], timeframes=["1h"], direction=direction
        )
    )
    return seen.get("direction_filter", "<not called>"), out


@pytest.mark.parametrize(
    "combo, direction, expected_filter, expected_row_direction",
    [
        ("COMBO_02_V2", "LONG", "LONG", "LONG"),
        ("COMBO_02_V2", "SHORT", "SHORT", "SHORT"),
        ("COMBO_02_V2", "BOTH", None, "BOTH"),
        ("COMBO_02_V2_1B", "SHORT", "SHORT", "SHORT"),
        ("COMBO_02_V2_1_A", "LONG", None, "LONG"),
        ("COMBO_02", "SHORT", "SHORT", "SHORT"),
        # BOTH is not a two-sided request outside the V2 family (falls back as before).
        ("COMBO_02", "BOTH", "LONG", "LONG"),
    ],
)
def test_strategy_matrix_honours_direction_for_v2_family(
    monkeypatch, combo, direction, expected_filter, expected_row_direction
):
    walk_filter, out = _matrix_direction_filter(monkeypatch, combo, direction)
    assert walk_filter == expected_filter
    assert out["direction"] == expected_row_direction
    assert out["rows"][0]["direction"] == expected_row_direction


# ---------------------------------------------------------------- shared causal fixtures


def _bar(t: datetime, o: float = 100.0, h: float = 100.5, l: float = 99.5, c: float = 100.0) -> dict:
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}


def _series(n: int, *, skip: tuple[int, ...] = ()) -> list[dict]:
    """``n`` hourly bars; hours listed in ``skip`` are missing (timestamp gaps)."""
    out = []
    hour = 0
    while len(out) < n:
        if hour not in skip:
            out.append(_bar(T0 + timedelta(hours=hour)))
        hour += 1
    return out


def _trade(entry_index: int, *, direction="LONG", entry=100.0, stop=95.0, tp1=110.0) -> dict:
    return {
        "symbol": "SOLUSDT",
        "timeframe": "1h",
        "combination_id": "COMBO_02_V2",
        "entry_index": entry_index,
        "signal_time": (T0 + timedelta(hours=entry_index)).isoformat(),
        "direction": direction,
        "entry_price": entry,
        "stop_price": stop,
        "tp1": tp1,
        "exit_index": entry_index + 1,
        "exit_price": tp1,
        "outcome": "TP1",
        "r_multiple": 2.0,
        "condition_snapshot": {"entry_type": "MARKET"},
    }


def _cfg() -> ResearchConfig:
    cfg = ResearchConfig()
    cfg.risk_usd = 20.0
    cfg.entry_slippage_rate = SLIP
    cfg.exit_slippage_rate = SLIP
    return cfg


# ---------------------------------------------------------------- fix 3: one causal position


def _overlap_candles() -> list[dict]:
    bars = _series(40)
    # Trade A (signal 0, fill 1) reaches TP1 110 on bar 12.
    bars[12] = _bar(bars[12]["time"], 100.0, 111.0, 99.5, 110.5)
    # The trade at signal 12 (fill 13) stops at 95 on bar 25.
    bars[25] = _bar(bars[25]["time"], 100.0, 100.5, 94.0, 94.5)
    return bars


def test_causal_book_holds_one_position_and_records_the_skips():
    candles = _overlap_candles()
    # Legacy walk released its lock early (legacy exits at entry+1), so B and C
    # are causal fills inside A (fill 1 -> exit 12). C fills on A's exit bar.
    trades = [_trade(0), _trade(5), _trade(11), _trade(12), _trade(26)]
    book = compare_execution_models(trades, candles, _cfg(), account_equity=1000.0)

    causal = book["causal"]["trades"]
    assert [t["entry_index"] for t in causal] == [0, 12, 26]
    assert [(t["fill_index"], t["exit_index"], t["outcome"]) for t in causal][:2] == [(1, 12, "TP1"), (13, 25, "SL")]
    for prev, nxt in zip(causal, causal[1:]):
        assert nxt["fill_index"] > prev["exit_index"]

    skipped = book["causal"]["skipped"]
    assert [(s["entry_index"], s["causal_skip_reason"]) for s in skipped] == [
        (5, SKIP_POSITION_OPEN),
        (11, SKIP_POSITION_OPEN),
    ]
    assert all(s["blocked_by"]["entry_index"] == 0 for s in skipped)
    assert book["reconciliation"]["causal_skipped"] == {SKIP_POSITION_OPEN: 2}
    assert book["reconciliation"]["causal"]["trades"] == 2  # the third is still OPEN
    # The legacy book is unchanged: every input trade stays.
    assert len(book["legacy"]["trades"]) == 5
    assert book["reconciliation"]["legacy"]["trades"] == 5


def test_skipped_causal_fills_are_on_the_row_not_in_the_blotter():
    clear_isolated_execution_runs()
    candles = _overlap_candles()
    record = build_execution_run_record(
        run_id="t",
        symbol="SOLUSDT",
        timeframe="1h",
        candles=candles,
        trades=[_trade(0), _trade(5), _trade(20)],
        config=_cfg(),
        account_equity=1000.0,
    )
    row: dict = {}
    apply_causal_primary_display(row, record)
    assert [t["entry_index"] for t in row["trades"]] == [0, 20]
    assert [t["entry_index"] for t in row["causal_skipped_trades"]] == [5]
    assert row["sample_size"] == 2
    assert row["optimistic_comparison"]["trades"] == 3


def test_open_causal_trade_blocks_every_later_fill():
    candles = _series(30)
    # A never reaches a level: OPEN through the last bar.
    book = compare_execution_models([_trade(2), _trade(10)], candles, _cfg(), account_equity=1000.0)
    assert [t["outcome"] for t in book["causal"]["trades"]] == ["OPEN"]
    assert [s["entry_index"] for s in book["causal"]["skipped"]] == [10]


# ---------------------------------------------------------------- fix 4: gap ends the trade


def test_gap_inside_a_trade_is_a_labelled_exit_at_the_next_open():
    # Hours 6 and 7 are missing: index 5 is 05:00, index 6 is 08:00.
    candles = _series(30, skip=(6, 7))
    candles[6] = _bar(candles[6]["time"], 97.0, 98.0, 96.5, 97.5)
    row = execute_causal_trade(_trade(2), candles, entry_slippage_rate=SLIP, exit_slippage_rate=SLIP)
    assert row["outcome"] == OUTCOME_GAP_EXIT
    assert (row["fill_index"], row["exit_index"]) == (3, 6)
    assert row["exit_price"] == pytest.approx(97.0 * (1 - SLIP))
    assert row["gap_last_bar_time"] == (T0 + timedelta(hours=5)).isoformat()
    assert row["gap_resume_time"] == (T0 + timedelta(hours=8)).isoformat()
    assert row["gap_missing_bars"] == 2
    assert row["exit_time"] == (T0 + timedelta(hours=8)).isoformat()
    # The post-gap bar's range is not held; only its open counts.
    assert row["mae"] == pytest.approx(100.0 * (1 + SLIP) - 97.0)


def test_gap_exit_counts_in_totals_and_keeps_the_lock():
    candles = _series(30, skip=(6, 7))
    trades = [_trade(2), _trade(4), _trade(6), _trade(10)]
    book = compare_execution_models(trades, candles, _cfg(), account_equity=1000.0)
    causal = book["causal"]["trades"]
    # Trade at signal 4 fills inside the gapped trade; signal 6 fills at 7, after its exit (6).
    assert [t["entry_index"] for t in causal][:2] == [2, 6]
    assert causal[0]["outcome"] == OUTCOME_GAP_EXIT
    assert causal[0]["net_pnl_usd"] is not None and causal[0]["net_pnl_usd"] < 0
    # Signal 10 fills while the OPEN trade from signal 6 is held to the last bar.
    assert [s["entry_index"] for s in book["causal"]["skipped"]] == [4, 10]
    summary = book["reconciliation"]["causal"]
    assert summary["trades"] == 1
    assert summary["net_pnl"] == pytest.approx(causal[0]["net_pnl_usd"])


def test_gap_exit_hits_in_row_metrics():
    from app.research.causal_execution import causal_r_metrics

    candles = _series(30, skip=(6, 7))
    book = compare_execution_models([_trade(2)], candles, _cfg(), account_equity=1000.0)
    metrics = causal_r_metrics(book["causal"]["trades"])
    assert metrics["gap_exit_hits"] == 1
    assert len(metrics["r_values"]) == 1


def test_signal_whose_next_bar_is_missing_never_fills_and_is_recorded():
    candles = _series(30, skip=(3,))  # index 2 is 02:00, index 3 is 04:00
    book = compare_execution_models([_trade(2), _trade(5)], candles, _cfg(), account_equity=1000.0)
    assert [t["entry_index"] for t in book["causal"]["trades"]] == [5]
    skipped = book["causal"]["skipped"]
    assert [(s["entry_index"], s["causal_skip_reason"], s["entry_price"]) for s in skipped] == [
        (2, SKIP_FILL_BAR_GAP, None)
    ]
