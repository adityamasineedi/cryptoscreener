"""LCSC v1 ETHUSDT forward runner: parity with the archived study, gates, kill/pass rules, records."""

from __future__ import annotations

import json
import pickle
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1] / "scripts" / "lcsc_validation"
sys.path.insert(0, str(HERE))

import forward as F  # noqa: E402
import study as S  # noqa: E402

UTC = timezone.utc
ARCHIVE = HERE / "out"
NEED_ARCHIVE = pytest.mark.skipif(not (ARCHIVE / "candles_ETHUSDT_1h.pkl").exists(),
                                  reason="archived LCSC candles not present")


def trade(i, r, trend="TREND_UP", usd=None):
    t = datetime(2026, 11, 1, tzinfo=UTC) + timedelta(days=6 * i)
    return {"signal_time": t.isoformat(), "exit_time": (t + timedelta(hours=40)).isoformat(), "R": r,
            "net_usd": 20 * r if usd is None else usd, "trend": trend, "fees_usd": 1.0, "slippage_usd": 0.5,
            "funding_usd": -0.2, "outcome": "TP" if r > 0 else "SL"}


def book(n=40, win=1.9, loss=-1.1, every=2, trends=F.TREND_REGIMES):
    return [trade(i, win if i % every == 0 else loss, trend=trends[i % len(trends)]) for i in range(n)]


AFTER = F.EARLIEST_EVALUATION + timedelta(days=1)


def ev(closed, now=AFTER, exceptions=(), drift_list=(), diagnostic=False):
    return F.evaluate(closed, now=now, open_exceptions=list(exceptions), drift_list=list(drift_list),
                      data_fp="x", diagnostic=diagnostic)


# ---- parity with the archived simulator -------------------------------------------------


@NEED_ARCHIVE
def test_recorded_walk_reproduces_the_archived_study_walk():
    s = S.Series("ETHUSDT")
    cands = S.candidates(s, 48, 3)  # archived cache
    end = len(s.c)
    archived = S.walk(s, [(s.eval_start, end, S.V1, cands)], data_end=end)
    fates, recorded = F.walk_recorded(s, cands, data_end=end)
    assert [(t["i"], t["exit_i"], t["outcome"]) for t in recorded] == [(t["i"], t["exit_i"], t["outcome"]) for t in archived]
    assert [t["FULL"]["R"] for t in recorded] == [t["FULL"]["R"] for t in archived]
    closed = [t for t in recorded if t["outcome"] != "OPEN"]
    assert len(closed) == 189  # archived ETH full range, FULL costs: +22.07R, +$474
    assert abs(sum(t["FULL"]["R"] for t in closed) - 22.07) < 0.01
    assert abs(sum(t["FULL"]["net_usd"] for t in closed) - 474) < 1.0
    assert {f["fate"] for f in fates} >= {"TAKEN", "BLOCKED_OPEN_TRADE"}
    rec = F.trade_record(s, closed[0])
    assert abs(sum(p["usd"] for p in rec["funding_payments"]) - rec["funding_usd"]) < 1e-9
    assert rec["trend"] in F.TREND_REGIMES and rec["fees_usd"] > 0


@NEED_ARCHIVE
def test_forward_series_matches_the_study_series_and_starts_at_the_forward_bar():
    full = pickle.loads((ARCHIVE / "candles_ETHUSDT_1h.pkl").read_bytes())
    funding = json.loads((ARCHIVE / "funding_ETHUSDT.json").read_text())
    anchored = [c for c in full if c["time"] >= F.SERIES_ANCHOR]
    s_old = S.Series("ETHUSDT")
    s_new = F.build_series(anchored, funding, forward_start=datetime(2023, 1, 1, tzinfo=UTC))
    assert s_new.t[0] == F.SERIES_ANCHOR == s_old.t[0]
    assert s_new.eval_start == s_old.eval_start
    assert (s_new.trend == s_old.trend).all() and (s_new.vol == s_old.vol).all()
    late = F.build_series(anchored, funding, forward_start=datetime(2026, 10, 1, tzinfo=UTC))
    assert late.t[late.eval_start] == datetime(2026, 10, 1, tzinfo=UTC)


# ---- gates --------------------------------------------------------------------------------


def test_refuses_before_nine_months():
    with pytest.raises(F.Refused) as exc:
        ev(book(60), now=F.EARLIEST_EVALUATION - timedelta(hours=1))
    assert exc.value.reason == "GATES_NOT_MET"


def test_refuses_below_forty_trades():
    with pytest.raises(F.Refused):
        ev(book(39))


def test_twelve_months_with_too_few_trades_is_a_kill():
    read = ev(book(39), now=F.TWELVE_MONTHS + timedelta(days=1))
    assert read["decision"] == "KILL" and "FEWER_THAN_40_TRADES_AFTER_12_MONTHS" in read["kills"]


def test_diagnostic_read_is_labelled_and_never_accepted():
    read = ev(book(10), now=F.FORWARD_START + timedelta(days=30), diagnostic=True)
    assert read["label"] == "NON_ACCEPTANCE_DIAGNOSTIC" and read["acceptance"] is False and read["gates_met"] is False


# ---- pass and kill rules ---------------------------------------------------------------------


def test_pass_to_paper_needs_every_rule():
    read = ev(book(40))  # 50% x 1.9R / 1.1R: +0.40R, PF 1.73, all three regimes
    assert read["decision"] == "PASS_TO_PAPER_ELIGIBLE" and read["acceptance"] is True
    assert all(read["passes"].values())
    for k in ("code", "config", "data"):
        assert read["fingerprints"][k]


def test_kill_rules():
    assert "EXPECTANCY_NOT_POSITIVE" in ev(book(40, win=1.0, loss=-1.0))["kills"]
    assert "PF_BELOW_1" in ev(book(40, win=0.9, loss=-1.0))["kills"]
    ten_losses = [trade(i, -1.6) for i in range(10)] + [
        trade(10 + i, 1.9 if i % 2 == 0 else -1.1, trend=F.TREND_REGIMES[i % 3]) for i in range(40)]  # 16R run first
    assert "DRAWDOWN_ABOVE_15R" in ev(ten_losses)["kills"]
    downtrend_only = [trade(i, 1.9 if i % 2 == 0 else -1.1, trend="TREND_DOWN") for i in range(20)] + \
        [trade(20 + i, -0.2, trend=("TREND_UP", "RANGE")[i % 2]) for i in range(20)]
    assert "PROFIT_ONLY_FROM_DOWNTREND" in ev(downtrend_only)["kills"]
    read = ev(book(40), exceptions=[{"id": "FUNDING_GAP:x"}])
    assert read["decision"] == "KILL" and "UNRESOLVED_EXCEPTIONS" in read["kills"]


def test_grey_zone_is_not_a_pass():
    read = ev(book(40, win=1.35, loss=-1.1))  # (1.35 - 1.1) / 2 = +0.125R, PF 1.23
    assert read["decision"] == "PASS_TO_PAPER_ELIGIBLE"
    grey = ev(book(40, win=1.25, loss=-1.1))  # +0.075R, PF 1.14: not killed, not passed
    assert grey["decision"] == "NO_PASS_CONTINUE" and grey["acceptance"] is False


def test_two_regime_rule():
    one_regime = [trade(i, 1.9 if i % 2 == 0 else -1.1, trend="TREND_UP") for i in range(40)]
    read = ev(one_regime)
    assert read["passes"]["positive_in_two_trend_regimes"] is False and read["decision"] == "NO_PASS_CONTINUE"


def test_drift_requires_a_restart():
    read = ev(book(40), drift_list=["code:backend/scripts/lcsc_validation/study.py"])
    assert read["decision"] == "CONFIG_DRIFT_RESTART_REQUIRED" and read["acceptance"] is False


# ---- records and exceptions -------------------------------------------------------------------


def test_revision_checks_flag_changed_bars_and_trades():
    prev = {"day_hashes": {"2026-10-16": "a", "2026-10-17": "b"},
            "closed_trades": [{"signal_time": "s1", "R": 1.9, "exit_time": "e1"}]}
    out = F.revision_checks(prev, {"2026-10-16": "CHANGED", "2026-10-17": "b2"},
                            [{"signal_time": "s1", "R": -1.1, "exit_time": "e1", "outcome": "SL"}])
    ids = {e["id"] for e in out}
    assert "DATA_REVISION:2026-10-16" in ids and "RECORD_CHANGED:s1" in ids
    assert "DATA_REVISION:2026-10-17" not in ids  # the last day of the previous run may still have been forming


def test_store_exceptions_stay_open_until_explained(tmp_path):
    store = F.Store(tmp_path)
    store.add_exception({"id": "DATA_GAP:x", "category": "DATA_GAP"})
    store.add_exception({"id": "DATA_GAP:x", "category": "DATA_GAP"})
    assert len(store.open_exceptions()) == 1
    with pytest.raises(ValueError):
        store.resolve("DATA_GAP:x", "", by="me")
    store.resolve("DATA_GAP:x", "exchange maintenance; bar confirmed missing on Binance", by="me")
    assert store.open_exceptions() == []


def test_contract_terms_are_the_frozen_terms():
    t = F.contract_terms()
    assert t["symbol"] == "ETHUSDT" and t["side"] == "LONG" and t["forward_start"] == "2026-10-16T00:00:00+00:00"
    assert t["min_closed_trades"] == 40 and t["earliest_evaluation"] == "2027-07-16T00:00:00+00:00"
    assert t["rules"] == {"min_rr": 2.0, "atr_multiplier": 1.5, "lookback": 48, "swing": 3}
    assert t["cost_model"] == {"next_open": True, "slip_entry": 0.0005, "slip_stop": 0.0010, "fee": 0.0004,
                               "funding": True, "gap": True}
    assert t["twelve_month_exception_preapproved"] is False
