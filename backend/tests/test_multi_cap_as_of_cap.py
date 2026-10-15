"""The multi-cap research API classifies each signal by the cap as of its signal time.

Fakes only: stored cap observations, OHLCV and candidate generation are injected.
No database, no network. Synthetic fixtures, not performance claims.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

import app.research.multi_cap_strategies.adapter as adapter_mod
import app.research.multi_cap_strategies.service as service_mod
from app.research.historical_market_cap.classifier import cap_group_at
from app.research.multi_cap_strategies.adapter import MultiCapStrategyAdapter
from app.research.multi_cap_strategies.config import MultiCapResearchConfig
from app.research.multi_cap_strategies.large_cap_sweep_choch import STRATEGY_ID as LARGE_ID
from app.research.multi_cap_strategies.mid_cap_fvg_discount import STRATEGY_ID as MID_ID
from app.research.multi_cap_strategies.runner import run_strategy_on_series
from app.research.multi_cap_strategies.schemas import StrategyCandidate
from app.research.multi_cap_strategies.service import MultiCapStrategiesService
from app.research.multi_cap_strategies.small_cap_volume_bos import STRATEGY_ID as SMALL_ID

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
N_BARS = 400
# The cap changes on the bar at index 200.
FLIP_INDEX = 200
EARLY = [80, 120, 160]
LATE = [240, 280, 320]
SIGNAL_INDEXES = EARLY + LATE

MID_CAP_VALUE = 5_000_000_000.0
LARGE_CAP_VALUE = 20_000_000_000.0


def _bar_time(i: int) -> datetime:
    return T0 + timedelta(minutes=15 * i)


def _candles() -> list[dict[str, Any]]:
    """Steady climb, so every long reaches its target a few bars after entry."""
    out = []
    for i in range(N_BARS):
        p = 100.0 + 0.5 * i
        out.append({"time": _bar_time(i), "open": p, "high": p + 0.6, "low": p - 0.1,
                    "close": p + 0.5, "volume": 100.0})
    return out


def _obs(symbol: str, effective_time: datetime, market_cap: float) -> dict[str, Any]:
    return {"symbol": symbol, "effective_time": effective_time, "market_cap": market_cap,
            "source": "test", "currency": "usd"}


OBSERVATIONS: dict[str, list[dict[str, Any]]] = {
    # MID until the flip bar, LARGE from it.
    "FLIPUSDT": [
        _obs("FLIPUSDT", T0 - timedelta(days=30), MID_CAP_VALUE),
        _obs("FLIPUSDT", _bar_time(FLIP_INDEX), LARGE_CAP_VALUE),
    ],
    # History starts at the flip bar.
    "LATEUSDT": [_obs("LATEUSDT", _bar_time(FLIP_INDEX), LARGE_CAP_VALUE)],
    "BTCUSDT": [_obs("BTCUSDT", T0 - timedelta(days=30), 1_000_000_000_000.0)],
    "ETHUSDT": [_obs("ETHUSDT", T0 - timedelta(days=30), 300_000_000_000.0)],
    "NOCAPUSDT": [],
}


def _fake_generator(strategy_id: str):
    class _Gen:
        @staticmethod
        def generate_candidates(symbol, timeframe, candles, *, config,
                                index_start=None, index_end=None):
            return [
                StrategyCandidate(
                    strategy_id=strategy_id,
                    symbol=symbol.upper(),
                    timeframe=timeframe,
                    direction="LONG",
                    entry_index=i,
                    signal_time=candles[i]["time"].isoformat(),
                    entry_price=float(candles[i]["open"]),
                    structural_invalidation=None,
                    metadata={"atr": 1.0},
                )
                for i in SIGNAL_INDEXES
            ]

    return _Gen


@pytest.fixture
def rig(monkeypatch):
    """Service with a fake cap-history loader, fake OHLCV and fixed candidates."""
    for sid in (LARGE_ID, MID_ID, SMALL_ID):
        monkeypatch.setitem(adapter_mod._GENERATORS, sid, _fake_generator(sid))

    ohlcv_loads: list[str] = []
    history_loads: list[list[str]] = []
    store_reads: list[str] = []

    async def _tail(symbol, timeframe, *, limit):
        ohlcv_loads.append(symbol)
        return _candles()

    async def _history(symbols):
        history_loads.append(list(symbols))
        return {s.upper(): list(OBSERVATIONS.get(s.upper(), [])) for s in symbols}

    def _store_cap(symbol):
        store_reads.append(symbol)
        return None

    monkeypatch.setattr(service_mod, "load_ohlcv_series_tail", _tail)
    monkeypatch.setattr(service_mod, "market_cap_from_store", _store_cap)
    service = MultiCapStrategiesService(observations_loader=_history)

    async def _run(symbols, **kwargs):
        return await service.run(symbols=symbols, timeframes=["15m"], persist=False, **kwargs)

    class _Rig:
        run = staticmethod(_run)

    _Rig.ohlcv_loads = ohlcv_loads
    _Rig.history_loads = history_loads
    _Rig.store_reads = store_reads
    return _Rig


def _series(payload: dict[str, Any], strategy_id: str, symbol: str) -> dict[str, Any]:
    rows = [r for r in payload["series_runs"]
            if r["strategy_id"] == strategy_id and r["symbol"] == symbol]
    assert len(rows) == 1
    return rows[0]


def _entries(run: dict[str, Any]) -> list[int]:
    return sorted(t["entry_index"] for t in run["trades"])


# --- as-of mode (default) ---------------------------------------------------------


async def test_default_mode_is_as_of_and_reads_history_once(rig):
    payload = await rig.run(["FLIPUSDT", "NOCAPUSDT"])
    assert payload["status"] == "OK"
    assert payload["cap_mode"] == "AS_OF"
    assert "as of its signal time" in payload["cap_mode_note"]
    assert rig.history_loads == [["FLIPUSDT", "NOCAPUSDT"]]
    # Today's cap is never read in this mode.
    assert rig.store_reads == []
    json.dumps(payload)


async def test_large_strategy_keeps_only_signals_from_the_large_period(rig):
    payload = await rig.run(["FLIPUSDT"], strategy_ids=[LARGE_ID])
    run = _series(payload, LARGE_ID, "FLIPUSDT")
    assert run["status"] == "OK"
    assert run["signal_count"] == 3
    assert _entries(run) == LATE
    for trade in run["trades"]:
        assert trade["asset_group"] == "large-cap"
        cap = trade["condition_snapshot"]["cap_as_of"]
        assert cap["strategy_cap_group"] == "LARGE_CAP"
        assert cap["market_cap"] == LARGE_CAP_VALUE
        assert cap["effective_time"] == _bar_time(FLIP_INDEX).isoformat()
        assert cap["as_of"] == trade["signal_time"]
    assert run["cap_mode"] == "AS_OF"
    assert run["resolved_cap_group"] == "AS_OF_PER_SIGNAL"


async def test_mid_strategy_keeps_only_signals_from_the_mid_period(rig):
    payload = await rig.run(["FLIPUSDT"], strategy_ids=[MID_ID])
    run = _series(payload, MID_ID, "FLIPUSDT")
    assert _entries(run) == EARLY
    for trade in run["trades"]:
        assert trade["asset_group"] == "mid-cap"
        cap = trade["condition_snapshot"]["cap_as_of"]
        assert cap["strategy_cap_group"] == "MID_CAP"
        assert cap["market_cap"] == MID_CAP_VALUE


async def test_stats_show_kept_and_cross_cap_exclusions_per_strategy_and_symbol(rig):
    payload = await rig.run(["FLIPUSDT"])
    block = payload["as_of_cap_filter"]
    assert set(block) == {LARGE_ID, MID_ID, SMALL_ID}
    for sid, group in ((LARGE_ID, "LARGE_CAP"), (MID_ID, "MID_CAP")):
        assert block[sid]["required_group"] == group
        assert block[sid]["by_symbol"]["FLIPUSDT"]["15m"] == {
            "signals_detected": 6,
            "cap_eligible_signals": 3,
            "cross_cap_signal_count": 3,
            "excluded_by_cap": 3,
            "excluded_by_missing_cap": 0,
            "excluded_by_missing_time": 0,
        }
        assert block[sid]["totals"] == block[sid]["by_symbol"]["FLIPUSDT"]["15m"]
        assert block[sid]["symbols_excluded_missing_cap_history"] == []
    # Never small in the window: every signal is cross-cap for the small strategy.
    small = block[SMALL_ID]["by_symbol"]["FLIPUSDT"]["15m"]
    assert (small["cap_eligible_signals"], small["cross_cap_signal_count"]) == (0, 6)
    assert _series(payload, SMALL_ID, "FLIPUSDT")["trades"] == []
    # The same numbers ride on the series run.
    assert _series(payload, LARGE_ID, "FLIPUSDT")["as_of_cap_filter"] == (
        block[LARGE_ID]["by_symbol"]["FLIPUSDT"]["15m"]
    )


async def test_symbol_without_stored_history_has_no_trades_and_is_reported(rig):
    payload = await rig.run(["FLIPUSDT", "NOCAPUSDT"])
    for sid in (LARGE_ID, MID_ID, SMALL_ID):
        assert not [r for r in payload["series_runs"] if r["symbol"] == "NOCAPUSDT"]
        block = payload["as_of_cap_filter"][sid]
        assert block["symbols_excluded_missing_cap_history"] == ["NOCAPUSDT"]
        assert "NOCAPUSDT" not in block["by_symbol"]
        elig = payload["eligibility"][sid]
        assert elig["eligible_symbols"] == ["FLIPUSDT"]
        assert [(r["symbol"], r["reason"], r["detail"]) for r in elig["unknown_symbols"]] == [
            ("NOCAPUSDT", "CAP_GROUP_UNAVAILABLE", "NO_STORED_CAP_HISTORY")
        ]
        assert elig["excluded_symbols"] == elig["unknown_symbols"]
    for result in payload["results"]:
        assert [r["symbol"] for r in result["unknown_symbols"]] == ["NOCAPUSDT"]
    # Never loaded, never given today's cap.
    assert "NOCAPUSDT" not in rig.ohlcv_loads
    assert rig.store_reads == []


async def test_only_symbols_without_history_yield_zero_trades(rig):
    payload = await rig.run(["NOCAPUSDT", "MISSINGUSDT"])
    assert payload["status"] == "OK"
    assert payload["series_runs"] == []
    assert rig.ohlcv_loads == []
    for result in payload["results"]:
        assert result["trade_count"] == 0
        assert result["eligible_symbols"] == []
    for block in payload["as_of_cap_filter"].values():
        assert block["symbols_excluded_missing_cap_history"] == ["NOCAPUSDT", "MISSINGUSDT"]
        assert block["totals"] == {}


async def test_signal_before_the_first_observation_is_unavailable_not_classified(rig):
    payload = await rig.run(["LATEUSDT"], strategy_ids=[LARGE_ID, MID_ID])
    large = _series(payload, LARGE_ID, "LATEUSDT")
    assert _entries(large) == LATE
    stats = payload["as_of_cap_filter"][LARGE_ID]["by_symbol"]["LATEUSDT"]["15m"]
    assert stats["excluded_by_missing_cap"] == 3
    assert stats["cross_cap_signal_count"] == 0
    assert stats["cap_eligible_signals"] == 3
    # The early signals are not handed to the mid strategy either.
    mid = payload["as_of_cap_filter"][MID_ID]["by_symbol"]["LATEUSDT"]["15m"]
    assert (mid["cap_eligible_signals"], mid["excluded_by_missing_cap"]) == (0, 3)
    assert mid["cross_cap_signal_count"] == 3
    assert _series(payload, MID_ID, "LATEUSDT")["trades"] == []


async def test_btc_and_eth_keep_the_existing_classifier_behaviour(rig):
    # Existing rule: BTC/ETH carry their own label and belong to no strategy group.
    for sym, label in (("BTCUSDT", "BTC"), ("ETHUSDT", "ETH")):
        lookup = cap_group_at(sym, _bar_time(100), OBSERVATIONS[sym])
        assert lookup.status == "OK"
        assert lookup.cap_group == label
        assert lookup.strategy_cap_group == "UNAVAILABLE"

    payload = await rig.run(["BTCUSDT", "ETHUSDT"])
    for sid in (LARGE_ID, MID_ID, SMALL_ID):
        for sym in ("BTCUSDT", "ETHUSDT"):
            assert _series(payload, sid, sym)["trades"] == []
            stats = payload["as_of_cap_filter"][sid]["by_symbol"][sym]["15m"]
            assert stats["signals_detected"] == 6
            assert stats["cap_eligible_signals"] == 0
            assert stats["excluded_by_missing_cap"] == 6
            assert stats["cross_cap_signal_count"] == 0


async def test_history_load_failure_is_an_error_not_a_fallback(rig):
    async def _boom(symbols):
        raise RuntimeError("db down")

    service = MultiCapStrategiesService(observations_loader=_boom)
    payload = await service.run(symbols=["FLIPUSDT"], timeframes=["15m"], persist=False)
    assert payload["status"] == "ERROR"
    assert payload["cap_mode"] == "AS_OF"
    assert "db down" in payload["reason"]
    assert rig.store_reads == []
    assert rig.ohlcv_loads == []


def test_default_loader_is_the_stored_observation_repository():
    from app.research.historical_market_cap.repository import load_observations_many

    assert MultiCapStrategiesService()._load_observations is load_observations_many


# --- explicit current-cap mode ------------------------------------------------------


async def test_market_caps_supplied_is_current_cap_mode_with_todays_behaviour(rig):
    payload = await rig.run(["FLIPUSDT", "NOCAPUSDT"], market_caps={"FLIPUSDT": LARGE_CAP_VALUE})
    assert payload["status"] == "OK"
    assert payload["cap_mode"] == "CURRENT_CAP"
    assert "one current market cap" in payload["cap_mode_note"]
    assert "as_of_cap_filter" not in payload
    # Stored history is not consulted; the symbol missing from the body reads the store.
    assert rig.history_loads == []
    assert rig.store_reads == ["NOCAPUSDT"]

    # One cap labels the whole window: all six signals trade as large-cap.
    run = _series(payload, LARGE_ID, "FLIPUSDT")
    assert _entries(run) == SIGNAL_INDEXES
    assert {t["asset_group"] for t in run["trades"]} == {"large-cap"}
    assert all("cap_as_of" not in t["condition_snapshot"] for t in run["trades"])
    assert run["resolved_cap_group"] == "large-cap"
    assert "cap_mode" not in run and "as_of_cap_filter" not in run
    assert payload["eligibility"][LARGE_ID]["eligible_symbols"] == ["FLIPUSDT"]
    assert not [r for r in payload["series_runs"] if r["strategy_id"] != LARGE_ID]

    # Same trades as the unchanged single-cap runner path.
    cfg = MultiCapResearchConfig()
    direct = run_strategy_on_series(
        strategy_id=LARGE_ID,
        symbol="FLIPUSDT",
        timeframe="15m",
        candles=_candles(),
        market_cap=LARGE_CAP_VALUE,
        config=cfg,
        adapter=MultiCapStrategyAdapter(cfg),
    )
    assert run["trades"] == direct["trades"]
    assert run["metrics"] == direct["metrics"]
    assert run["fee_enriched_trades"] == direct["fee_enriched_trades"]


async def test_explicit_current_flag_reads_the_store_cap(rig, monkeypatch):
    monkeypatch.setattr(service_mod, "market_cap_from_store", lambda symbol: MID_CAP_VALUE)
    payload = await rig.run(["FLIPUSDT"], cap_mode="current")
    assert payload["cap_mode"] == "CURRENT_CAP"
    assert rig.history_loads == []
    assert _entries(_series(payload, MID_ID, "FLIPUSDT")) == SIGNAL_INDEXES
    assert payload["eligibility"][LARGE_ID]["eligible_symbols"] == []


async def test_empty_market_caps_defaults_to_as_of(rig):
    payload = await rig.run(["FLIPUSDT"], market_caps={})
    assert payload["cap_mode"] == "AS_OF"


async def test_invalid_mode_combinations_are_errors(rig):
    unknown = await rig.run(["FLIPUSDT"], cap_mode="yesterday")
    assert unknown["status"] == "ERROR" and "cap_mode" in unknown["reason"]
    mixed = await rig.run(["FLIPUSDT"], cap_mode="AS_OF", market_caps={"FLIPUSDT": 1.0})
    assert mixed["status"] == "ERROR" and "CURRENT_CAP" in mixed["reason"]
    assert rig.ohlcv_loads == []
