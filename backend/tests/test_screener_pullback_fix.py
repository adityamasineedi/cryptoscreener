"""Screener pullback fix (owner D193(b), D220): synthetic candles through the real engines.

The shared ``SignalEngine`` measures the impulse on the evaluated bar and asks for pullback bars after it,
so a clear trend -> BOS impulse -> pullback to the broken level stays WAITING on every bar. The screener
engine takes the impulse from the BOS episode (research "+PBFIX" rule) and reaches ENTRY_CANDIDATE.
The payload paper reads stays the shared engine's result. Synthetic OHLCV is test-only.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services import decision_journal
from app.services.screener_pullback import SCREENER_PULLBACK_RULE, ScreenerSignalEngine, episode_impulse
from app.signals.bos_engine import detect_bos
from app.signals.config import SignalConfig
from app.signals.signal_engine import SignalEngine
from app.signals.swing_detector import swings_for_timeframe
from app.signals.trend_engine import infer_trend

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
ENTRY = ("LONG_ENTRY_CANDIDATE", "SHORT_ENTRY_CANDIDATE", "ENTRY_CANDIDATE")


def _series(*, mirror: bool = False) -> tuple[list[dict], list[dict], int]:
    """(context bars, setup bars, impulse index). Uptrend of HH/HL waves, a BOS impulse candle closing
    3 points above the last swing high on 8x volume, a pullback bar wicking to the broken level and
    closing above it, then one more bar. ``mirror`` reflects every price (a downtrend, SHORT)."""
    bars: list[dict] = []

    def add(o, h, l, c, v=1.0):
        bars.append({"time": T0 + timedelta(minutes=15 * len(bars)), "open": o, "high": h, "low": l,
                     "close": c, "volume": v})

    c = 100.0
    for _ in range(25):
        add(c, c + 0.15, c - 0.15, c)
    for _ in range(8):
        o = c + 0.2
        for _ in range(5):
            add(o, o + 1.15, o - 0.15, o + 1.0)
            o = c = o + 1.0
        o = c - 0.2
        for _ in range(3):
            add(o, o + 0.15, o - 1.15, o - 1.0)
            o = c = o - 1.0
    context = list(bars)
    level = max(b["high"] for b in bars[-8:])
    o = c + 0.2
    add(o, level + 3.1, o - 0.05, level + 3.0, v=8.0)  # impulse
    impulse_index = len(bars) - 1
    add(level + 2.9, level + 3.0, level - 0.05, level + 0.6)  # pullback to the broken level
    add(level + 0.6, level + 1.0, level + 0.3, level + 0.9)
    if mirror:
        k = 300.0
        flip = lambda b: {**b, "open": k - b["open"], "high": k - b["low"], "low": k - b["high"], "close": k - b["close"]}
        context = [flip(b) for b in context]
        bars = [flip(b) for b in bars]
    return context, bars, impulse_index


def _tfs(context: list[dict], setup: list[dict]) -> dict[str, list[dict]]:
    return {"4h": context, "1h": context, "15m": setup, "5m": context}


def _strip(d: dict) -> dict:
    out = json.loads(json.dumps(d, default=str))
    out.pop("calculated_at", None)
    return out


CFG = SignalConfig()


@pytest.mark.parametrize("mirror", [False, True])
def test_shared_engine_never_reaches_entry_candidate(mirror):
    """The defect: impulse at the evaluated bar, pullback needs later bars -> WAITING on every bar."""
    context, bars, imp = _series(mirror=mirror)
    eng = SignalEngine(CFG)
    reasons = set()
    for end in range(imp - 3, len(bars)):
        a = eng.analyze("SYN", _tfs(context, bars[: end + 1])).to_dict()
        assert a["pullback"]["pullback_state"] == "WAITING"
        assert a["status"] not in ENTRY
        reasons.add(a["pullback"]["reason"])
    assert "Waiting for bars after impulse" in reasons  # the impulse bar itself
    assert "Waiting for valid impulse after BOS" in reasons  # the pullback bars


@pytest.mark.parametrize("mirror,status", [(False, "LONG_ENTRY_CANDIDATE"), (True, "SHORT_ENTRY_CANDIDATE")])
def test_screener_engine_reaches_entry_candidate_on_the_pullback_bar(mirror, status):
    context, bars, imp = _series(mirror=mirror)
    a = ScreenerSignalEngine(CFG).analyze("SYN", _tfs(context, bars[: imp + 2])).to_dict()
    assert a["impulse"]["is_impulse"] and a["impulse"]["bar_index"] == imp
    assert a["impulse"]["pullback_rule"] == SCREENER_PULLBACK_RULE
    assert a["pullback"]["pullback_state"] == "CONFIRMED" and a["pullback"]["impulse_bar"] == imp
    assert 20.0 <= a["pullback"]["retracement_percentage"] <= 80.0
    assert a["status"] == status and a["direction"] == ("SHORT" if mirror else "LONG")
    assert a["stop"]["final_stop"] is not None and not a["stop"].get("provisional")
    assert a["entry"]["entry_price"] is not None and a["targets"]
    assert a["risk_reward"]["RISK_REWARD"] == "PASS"


def test_screener_engine_equals_shared_engine_until_there_are_bars_after_the_impulse():
    context, bars, imp = _series()
    shared, fixed = SignalEngine(CFG), ScreenerSignalEngine(CFG)
    for end in range(imp - 3, imp + 1):  # before the BOS, and the impulse bar itself (no earlier impulse)
        tfs = _tfs(context, bars[: end + 1])
        assert _strip(fixed.analyze("SYN", tfs).to_dict()) == _strip(shared.analyze("SYN", tfs).to_dict())


def test_other_timeframes_are_untouched():
    context, bars, imp = _series()
    window = bars[: imp + 2]
    for tf in ("1h", "4h", "5m"):
        a = SignalEngine(CFG).analyze_timeframe("SYN", tf, window)
        b = ScreenerSignalEngine(CFG).analyze_timeframe("SYN", tf, window)
        a.pop("_swings_objs"), b.pop("_swings_objs")
        assert _strip(a) == _strip(b)


def _bos_at(bars, end):
    sw = swings_for_timeframe(bars, CFG, "15m", symbol="SYN", as_of_index=end)
    tr = infer_trend(sw, close=bars[end]["close"], mode=CFG.trend_mode)
    return detect_bos(bars, sw, tr, symbol="SYN", timeframe="15m", atr_period=CFG.atr_period, as_of_index=end)


def test_episode_impulse_rules():
    _, bars, imp = _series()
    bos = _bos_at(bars, imp + 1)
    assert bos["state"] == "CONFIRMED"
    got = episode_impulse(bars, bos, CFG, imp + 1, timeframe="15m", symbol="SYN")
    assert got is not None and got["bar_index"] == imp
    # Two bars on: the latest impulse of the episode is still the impulse bar.
    got2 = episode_impulse(bars, _bos_at(bars, imp + 2), CFG, imp + 2, timeframe="15m", symbol="SYN")
    assert got2 is not None and got2["bar_index"] == imp
    # At the impulse bar the bar before has no BOS: the episode is empty, nothing earlier.
    assert episode_impulse(bars, _bos_at(bars, imp), CFG, imp, timeframe="15m", symbol="SYN") is None
    # A different broken level is a different episode.
    other = {**bos, "broken_level": bos["broken_level"] + 1.0}
    assert episode_impulse(bars, other, CFG, imp + 1, timeframe="15m", symbol="SYN") is None
    # Opposite direction: never matches.
    assert episode_impulse(bars, {**bos, "direction": "BEARISH_BOS"}, CFG, imp + 1, timeframe="15m", symbol="SYN") is None


def test_no_look_ahead_in_the_episode_scan():
    """Bars after ``end`` never change the result at ``end``."""
    context, bars, imp = _series()
    end = imp + 1
    short = ScreenerSignalEngine(CFG).analyze("SYN", _tfs(context, bars[: end + 1])).to_dict()
    longer = ScreenerSignalEngine(CFG).analyze("SYN", _tfs(context, bars), as_of_index_by_tf={"15m": end}).to_dict()
    for k in ("status", "direction", "impulse", "pullback", "retest", "entry", "stop", "targets"):
        assert json.dumps(short[k], sort_keys=True, default=str) == json.dumps(longer[k], sort_keys=True, default=str)


# --- the service: screener view vs the payload paper reads -------------------------------------------------


@pytest.fixture
def service(monkeypatch):
    from app.services import setup_signals as mod
    from app.services.ohlcv_store import ohlcv_store

    state: dict[str, dict[str, list[dict]]] = {"tfs": {}}
    monkeypatch.setattr(ohlcv_store, "get_candles_for_engine",
                        lambda sym, tf, include_open=False: list(state["tfs"].get(tf) or []))
    monkeypatch.setattr(ohlcv_store, "get_closed", lambda sym, tf, limit=None: [])
    svc = mod.SetupSignalService()
    svc.config = CFG
    svc.engine = SignalEngine(CFG)
    svc.screener_engine = ScreenerSignalEngine(CFG)
    return svc, state


def _replay(svc, state, context, bars, imp, symbol="SYNUSDT"):
    out = []
    for end in range(imp - 3, len(bars)):
        state["tfs"] = _tfs(context, bars[: end + 1])
        out.append(svc.analyze_symbol(symbol))
    return out


def test_service_payload_outside_screener_setup_is_the_shared_engine_result(service):
    svc, state = service
    context, bars, imp = _series()
    for end, p in zip(range(imp - 3, len(bars)), _replay(svc, state, context, bars, imp)):
        tfs = {**_tfs(context, bars[: end + 1]), "1m": []}  # the service also asks for 1m (none here)
        base = SignalEngine(CFG).analyze("SYNUSDT", tfs, rvol_by_tf={tf: None for tf in tfs},
                                         oi_status="N/A", liquidation_status="N/A").to_dict()
        shared = {k: v for k, v in p.items() if k not in ("screener_setup", "ohlcv_freshness", "triggered_timeframe")}
        assert _strip(shared) == _strip(base)
        assert p["status"] not in ENTRY  # paper's field: unchanged, still never an entry
    pull = _replay(svc, state, context, bars, imp)[4]  # imp + 1
    assert pull["screener_setup"]["status"] == "LONG_ENTRY_CANDIDATE"
    assert pull["screener_setup"]["episode_impulse_used"] is True


def test_screener_columns_and_journal_read_the_screener_view(service, tmp_path):
    svc, state = service
    context, bars, imp = _series()
    decision_journal.configure(tmp_path / "j.jsonl", signal_detail=True)
    try:
        payloads = _replay(svc, state, context, bars, imp)
    finally:
        decision_journal.configure(None)
    fields = svc.screener_fields("SYNUSDT")  # cache holds the last bar (imp + 2)
    assert fields["setup_signal"].value == "LONG_ENTRY_CANDIDATE"
    assert fields["setup_pullback"].value in ("ACTIVE", "CONFIRMED")
    assert fields["setup_impulse"].value in ("MODERATE", "STRONG")
    assert fields["setup_entry"].value is not None and fields["setup_sl"].value is not None
    rows = [json.loads(x) for x in (tmp_path / "j.jsonl").read_text(encoding="utf-8").splitlines()]
    screener = [r for r in rows if r.get("book") == "SCREENER"]
    assert len(screener) == 2  # pullback bar and the bar after: one row per setup bar
    assert {r["direction"] for r in screener} == {"LONG"}
    assert all(r["pullback_rule"] == SCREENER_PULLBACK_RULE for r in screener)
    assert payloads[-1]["status"] not in ENTRY


def test_paper_decisions_identical_with_and_without_the_screener_view(service, monkeypatch):
    """Paper's legacy setup entry (Path A and Path B, legacy auto-entry on) gets the branch payload and the
    same payload without ``screener_setup`` (= what 1251e57 produces): same decisions and same state."""
    import app.services.paper_trade as paper_mod
    from app.services.paper_risk import PaperRiskPolicy
    from app.services.paper_trade import PaperTradeEngine

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 120.0)
    svc, state = service
    context, bars, imp = _series()
    payloads = _replay(svc, state, context, bars, imp)
    assert any((p.get("screener_setup") or {}).get("status") in ENTRY for p in payloads)
    for mode in ("path_a", "path_b"):
        results = []
        for with_view in (True, False):
            eng = PaperTradeEngine(enabled=True, entry_mode=mode, risk_policy=PaperRiskPolicy(enabled=False),
                                   v1_profile_enabled=False)
            eng.legacy_auto_entry_enabled = True
            got = []
            for p in payloads:
                q = copy.deepcopy(p)
                if not with_view:
                    q.pop("screener_setup", None)
                pos = eng.on_setup_signal("SYNUSDT", q)
                got.append(None if pos is None else pos.symbol)
            results.append((got, sorted(eng.open_symbols())))
        assert results[0] == results[1]
        assert results[0][1] == []  # the shared status never opens a paper trade here
