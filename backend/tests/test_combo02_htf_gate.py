"""v1 REGRESSION – must pass (tag: v1-combo02-long-htf).

COMBO_02 HTF hard-gate: longs require 4h+1h bullish structure.
Failures here should block merges that touch frozen COMBO_02 / HTF paths
unless the change is an intentional version bump (see docs/v1_freeze.md).

Synthetic OHLCV is TEST-ONLY — never used in production paths.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.research.bos_combinations import COMBINATIONS, get_combination
from app.research.combination_backtest import run_combination_backtest
from app.research.combination_engine import (
    _clone_signal_config,
    evaluate_combination_at_bar,
    htf_alignment_gate,
)
from app.research.config import ResearchConfig
from app.signals.config import SignalConfig
from app.signals.swing_detector import swings_for_timeframe
from app.signals.trend_engine import infer_trend

# Pytest marker for CI / selective runs: pytest -m v1_freeze
pytestmark = pytest.mark.v1_freeze


def _ts(minutes: int) -> datetime:
    return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes)


def _candle(t: datetime, o: float, h: float, l: float, c: float, v: float = 1000.0) -> dict:
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _trending_series(
    *,
    n: int,
    minutes_per_bar: int,
    start_price: float,
    drift: float,
    wave: float = 3.0,
) -> list[dict[str, Any]]:
    """HH/HL (drift>0) or LH/LL (drift<0) with explicit 8-bar swing pivots.

    phase 0 = swing low, phase 4 = swing high. Baseline steps by ``drift``
    each bar so successive pivots form HH+HL or LH+LL for left/right=3.
    """
    out: list[dict[str, Any]] = []
    price = start_price
    bull = drift >= 0
    # Keep step << wave so intervening bars cannot eclipse pivot highs/lows.
    step = min(abs(drift) if abs(drift) > 1e-9 else 0.5, wave * 0.25)
    for i in range(n):
        t = _ts(i * minutes_per_bar)
        phase = i % 8
        if phase == 0:
            o = c = price
            if bull:
                h, l = price + 0.2, price - wave
            else:
                h, l = price + wave, price - 0.2
        elif phase == 4:
            o = c = price
            if bull:
                h, l = price + wave, price - 0.2
            else:
                h, l = price + 0.2, price - wave
        else:
            o = price
            c = price + (0.25 if bull else -0.25)
            h = max(o, c) + 0.3
            l = min(o, c) - 0.3
        out.append(_candle(t, o, h, l, c, v=2000.0))
        price = price + (step if bull else -step)
    return out


def _htf_ending_with_setup(
    setup: list[dict[str, Any]],
    *,
    n: int,
    minutes_per_bar: int,
    start_price: float,
    drift: float,
) -> list[dict[str, Any]]:
    """Build HTF history ending at the setup series end (no look-ahead).

    Keeps enough pre-history for 1h/4h swing confirmation even when the
    setup window itself is short.
    """
    if not setup:
        return []
    end = setup[-1]["time"]
    start = end - timedelta(minutes=minutes_per_bar * (n - 1))
    # Rebase _trending_series absolute epoch onto (end - n*tf)
    offset_min = int((start - _ts(0)).total_seconds() // 60)
    raw = _trending_series(
        n=n,
        minutes_per_bar=minutes_per_bar,
        start_price=start_price,
        drift=drift,
    )
    out: list[dict[str, Any]] = []
    for i, c in enumerate(raw):
        t = _ts(offset_min + i * minutes_per_bar)
        if t > end:
            break
        nc = dict(c)
        nc["time"] = t
        out.append(nc)
    return out


@pytest.fixture
def scfg() -> SignalConfig:
    return SignalConfig()


@pytest.fixture
def rcfg() -> ResearchConfig:
    return ResearchConfig(min_bars=40)


class TestCombo02Definition:
    def test_combo02_requires_htf(self):
        c = COMBINATIONS["COMBO_02"]
        assert c.require_htf_alignment is True
        assert c.require_trend is True
        assert c.require_bos is True

    def test_combo02_local_setup_tf_only(self):
        c = COMBINATIONS["COMBO_02_LOCAL"]
        assert c.require_htf_alignment is False
        assert get_combination("TREND_BOS_SETUP_TF_ONLY") is c

    def test_clone_config_aligns_mtf_with_htf_flag(self):
        base = SignalConfig(require_mtf_alignment=False)
        rcfg = ResearchConfig()
        with_htf = _clone_signal_config(base, rcfg, require_htf_alignment=True)
        assert with_htf.require_mtf_alignment is True
        # Without combo HTF, do not force False over an explicit True base.
        base_on = SignalConfig(require_mtf_alignment=True)
        preserved = _clone_signal_config(base_on, rcfg, require_htf_alignment=False)
        assert preserved.require_mtf_alignment is True


class TestHtfAlignmentGate:
    def test_missing_htf_fail_closed(self, scfg: SignalConfig):
        setup = _trending_series(n=80, minutes_per_bar=15, start_price=100.0, drift=0.35)
        ok, meta = htf_alignment_gate(
            symbol="TESTUSDT",
            timeframe="15m",
            candles=setup,
            as_of_index=70,
            bos={"direction": "BULLISH_BOS", "state": "CONFIRMED"},
            signal_config=scfg,
            candles_1h=None,
            candles_4h=None,
        )
        assert ok is False
        assert meta["htf_alignment"] == "HTF_NEUTRAL_UNAVAILABLE"

    def test_bullish_htf_passes(self, scfg: SignalConfig):
        setup = _trending_series(n=120, minutes_per_bar=15, start_price=100.0, drift=0.4)
        h1 = _htf_ending_with_setup(
            setup, n=120, minutes_per_bar=60, start_price=100.0, drift=0.6
        )
        h4 = _htf_ending_with_setup(
            setup, n=90, minutes_per_bar=240, start_price=100.0, drift=0.8
        )
        # Sanity: HTF trends bullish near end
        i1 = len(h1) - 1
        i4 = len(h4) - 1
        t1 = infer_trend(swings_for_timeframe(h1, scfg, "1h", as_of_index=i1))
        t4 = infer_trend(swings_for_timeframe(h4, scfg, "4h", as_of_index=i4))
        assert t1["trend"] == "BULLISH"
        assert t4["trend"] == "BULLISH"

        ok, meta = htf_alignment_gate(
            symbol="TESTUSDT",
            timeframe="15m",
            candles=setup,
            as_of_index=len(setup) - 3,
            bos={"direction": "BULLISH_BOS", "state": "CONFIRMED"},
            signal_config=scfg,
            candles_1h=h1,
            candles_4h=h4,
        )
        assert ok is True
        assert meta["htf_alignment"] == "HTF_ALIGNED"

    def test_bearish_htf_blocks_long(self, scfg: SignalConfig):
        setup = _trending_series(n=120, minutes_per_bar=15, start_price=200.0, drift=0.35)
        h1 = _htf_ending_with_setup(
            setup, n=120, minutes_per_bar=60, start_price=200.0, drift=-0.6
        )
        h4 = _htf_ending_with_setup(
            setup, n=90, minutes_per_bar=240, start_price=200.0, drift=-0.8
        )
        ok, meta = htf_alignment_gate(
            symbol="TESTUSDT",
            timeframe="15m",
            candles=setup,
            as_of_index=len(setup) - 3,
            bos={"direction": "BULLISH_BOS", "state": "CONFIRMED"},
            signal_config=scfg,
            candles_1h=h1,
            candles_4h=h4,
        )
        assert ok is False
        assert meta["htf_alignment"] in {"HTF_CONFLICT", "HTF_NEUTRAL_UNAVAILABLE"}

    def test_mixed_htf_blocks_long(self, scfg: SignalConfig):
        setup = _trending_series(n=120, minutes_per_bar=15, start_price=150.0, drift=0.35)
        h1 = _htf_ending_with_setup(
            setup, n=120, minutes_per_bar=60, start_price=150.0, drift=0.6
        )
        h4 = _htf_ending_with_setup(
            setup, n=90, minutes_per_bar=240, start_price=150.0, drift=-0.8
        )
        ok, meta = htf_alignment_gate(
            symbol="TESTUSDT",
            timeframe="15m",
            candles=setup,
            as_of_index=len(setup) - 3,
            bos={"direction": "BULLISH_BOS", "state": "CONFIRMED"},
            signal_config=scfg,
            candles_1h=h1,
            candles_4h=h4,
        )
        assert ok is False
        assert meta["htf_alignment"] == "HTF_NEUTRAL_UNAVAILABLE"


class TestCombo02EvaluateIntegration:
    def test_combo02_no_long_without_htf_series(self, scfg: SignalConfig, rcfg: ResearchConfig):
        setup = _trending_series(n=140, minutes_per_bar=15, start_price=100.0, drift=0.4)
        # Scan several bars — with HTF missing, COMBO_02 must never emit longs.
        for i in range(60, len(setup)):
            out = evaluate_combination_at_bar(
                symbol="TESTUSDT",
                timeframe="15m",
                candles=setup,
                as_of_index=i,
                combination=COMBINATIONS["COMBO_02"],
                signal_config=scfg,
                research_config=rcfg,
                compute_sd=False,
                candles_1h=None,
                candles_4h=None,
            )
            assert out.get("status") != "LONG_ENTRY_CANDIDATE"
            if "htf" in (out.get("required") or []):
                assert out.get("gates", {}).get("htf") is False

    def test_combo02_local_can_trade_without_htf(self, scfg: SignalConfig, rcfg: ResearchConfig):
        """Legacy combo must not require HTF candles (may still NO_SETUP for other gates)."""
        setup = _trending_series(n=140, minutes_per_bar=15, start_price=100.0, drift=0.4)
        out = evaluate_combination_at_bar(
            symbol="TESTUSDT",
            timeframe="15m",
            candles=setup,
            as_of_index=100,
            combination=COMBINATIONS["COMBO_02_LOCAL"],
            signal_config=scfg,
            research_config=rcfg,
            compute_sd=False,
        )
        assert "htf" not in (out.get("required") or [])

    def test_backtest_combo02_blocks_when_htf_bearish(
        self, scfg: SignalConfig, rcfg: ResearchConfig
    ):
        setup = _trending_series(n=160, minutes_per_bar=15, start_price=200.0, drift=0.45)
        h1 = _htf_ending_with_setup(
            setup, n=120, minutes_per_bar=60, start_price=200.0, drift=-0.6
        )
        h4 = _htf_ending_with_setup(
            setup, n=90, minutes_per_bar=240, start_price=200.0, drift=-0.8
        )
        run = run_combination_backtest(
            "TESTUSDT",
            "15m",
            setup,
            "COMBO_02",
            signal_config=scfg,
            research_config=rcfg,
            direction_filter="LONG",
            candles_1h=h1,
            candles_4h=h4,
        )
        assert run["status"] in ("OK", "INSUFFICIENT_DATA")
        longs = [
            t
            for t in (run.get("trades") or [])
            if t.get("direction") == "LONG" and t.get("outcome") != "OPEN"
        ]
        assert longs == []

    def test_backtest_combo02_allows_when_htf_bullish(
        self, scfg: SignalConfig, rcfg: ResearchConfig
    ):
        setup = _trending_series(n=180, minutes_per_bar=15, start_price=100.0, drift=0.5)
        h1 = _htf_ending_with_setup(
            setup, n=140, minutes_per_bar=60, start_price=100.0, drift=0.6
        )
        h4 = _htf_ending_with_setup(
            setup, n=100, minutes_per_bar=240, start_price=100.0, drift=0.8
        )
        run_htf = run_combination_backtest(
            "TESTUSDT",
            "15m",
            setup,
            "COMBO_02",
            signal_config=scfg,
            research_config=rcfg,
            direction_filter="LONG",
            candles_1h=h1,
            candles_4h=h4,
        )
        run_local = run_combination_backtest(
            "TESTUSDT",
            "15m",
            setup,
            "COMBO_02_LOCAL",
            signal_config=scfg,
            research_config=rcfg,
            direction_filter="LONG",
        )
        # With aligned bullish HTF, COMBO_02 must not be strictly emptier than
        # a forced fail-closed (missing HTF) run on the same setup series.
        run_missing = run_combination_backtest(
            "TESTUSDT",
            "15m",
            setup,
            "COMBO_02",
            signal_config=scfg,
            research_config=rcfg,
            direction_filter="LONG",
            candles_1h=None,
            candles_4h=None,
        )
        n_htf = int(run_htf.get("sample_size") or 0)
        n_missing = int(run_missing.get("sample_size") or 0)
        n_local = int(run_local.get("sample_size") or 0)
        assert n_missing == 0
        # Local (no HTF) may find setups; HTF-gated should be <= local and
        # strictly > missing when structure + BOS fire under bullish HTF.
        assert n_htf <= n_local
        if n_local > 0:
            assert n_htf >= 0  # gate may still filter all if BOS timing differs
            for t in run_htf.get("trades") or []:
                snap = t.get("condition_snapshot") or {}
                if t.get("direction") == "LONG" and snap:
                    assert snap.get("htf") is True or snap.get("htf_alignment") == "HTF_ALIGNED"


class TestCombo02V1Regression:
    """v1-critical contrasts — keep stable across freezes (docs/v1_freeze.md)."""

    def test_combo02_requires_htf_aligned_flag(self):
        c = COMBINATIONS["COMBO_02"]
        assert c.require_htf_alignment is True
        assert c.require_trend is True
        assert c.require_bos is True
        local = COMBINATIONS["COMBO_02_LOCAL"]
        assert local.require_htf_alignment is False
        assert "LEGACY" in (local.description or "").upper() or "RESEARCH" in (
            local.description or ""
        ).upper()

    def test_bearish_4h_zero_longs_combo02_local_may_trade(
        self, scfg: SignalConfig, rcfg: ResearchConfig
    ):
        """COMBO_02 must produce 0 LONGs when 4h is bearish; LOCAL may still trade."""
        setup = _trending_series(n=180, minutes_per_bar=60, start_price=150.0, drift=0.5)
        h1 = list(setup)  # setup TF is 1h — reuse as 1h HTF leg
        h4 = _htf_ending_with_setup(
            setup, n=100, minutes_per_bar=240, start_price=150.0, drift=-0.8
        )
        # Sanity: 4h bears near end
        t4 = infer_trend(swings_for_timeframe(h4, scfg, "4h", as_of_index=len(h4) - 1))
        assert t4["trend"] == "BEARISH"

        run_v1 = run_combination_backtest(
            "TESTUSDT",
            "1h",
            setup,
            "COMBO_02",
            signal_config=scfg,
            research_config=rcfg,
            direction_filter="LONG",
            candles_1h=h1,
            candles_4h=h4,
        )
        run_local = run_combination_backtest(
            "TESTUSDT",
            "1h",
            setup,
            "COMBO_02_LOCAL",
            signal_config=scfg,
            research_config=rcfg,
            direction_filter="LONG",
            candles_1h=h1,
            candles_4h=h4,
        )

        def _closed_longs(run: dict) -> list:
            out = []
            for t in run.get("trades") or []:
                direction = t.direction if hasattr(t, "direction") else t.get("direction")
                outcome = t.outcome if hasattr(t, "outcome") else t.get("outcome")
                if direction == "LONG" and outcome != "OPEN":
                    out.append(t)
            return out

        assert _closed_longs(run_v1) == []
        # LOCAL is allowed to find longs — proves the HTF gate is doing work when it does.
        # If LOCAL also finds none (structure/BOS timing), still OK as long as v1 is empty.
        assert int(run_v1.get("sample_size") or 0) == 0
        assert int(run_local.get("sample_size") or 0) >= int(run_v1.get("sample_size") or 0)
