"""Owner D188 part 3: screener_cost_ok_only (default false) sends SCREENER alerts only when cost as R is OK."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.alerts.config import CONFIG_PATH, AlertConfig
from app.alerts.data import MemoryData
from app.alerts.pipeline import SKIPPED_COST_R, AlertPipeline
from app.alerts.plan import OBSERVATION_PREFIX, Signal, build_plan
from app.alerts.sources import journal_items
from app.screener_tool.costs import cost_as_r

T0 = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)
TICKS = {"ETHUSDT": 0.01, "IOSTUSDT": 0.0001}
STATUS = {"default": "UNPROVEN", "sources": {"COMBO_02_V1": {"status": "PAPER"}}}


def cfg(**over) -> AlertConfig:
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    raw.update(over)
    return AlertConfig(raw, status_map=STATUS, ticks=TICKS, round_trip_cost=0.0018)


def screener(stop: float, *, source: str = "SCREENER", entry: float | None = 100.0) -> Signal:
    extra = {"live_outcome": "screener_signal" if source == "SCREENER" else "opened"}
    if entry is not None:
        extra["signal_entry_price"] = entry
    return Signal(source=source, strategy_id="SCREENER_SETUP" if source == "SCREENER" else source, symbol="ETHUSDT",
                  side="LONG", timeframe="15m", signal_bar=T0, decided_at=T0 + timedelta(minutes=16), stop=stop,
                  tested_target=103.0, signal_close=100.0, atr=0.01, target_tested=source != "SCREENER", extra=extra)


def handle(tmp_path, c, s):
    return AlertPipeline(c, out_dir=tmp_path, data=MemoryData()).handle(s, s.decided_at)


# 0.55% stop: clears the stop-distance and entry-to-stop filters, but costs 0.14% / 0.55% = 0.255 R.
TIGHT = 99.45
WIDE = 99.0  # 1% stop: 0.14 R


def test_flag_is_in_the_shipped_config_and_defaults_off():
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert raw["screener_cost_ok_only"] is False
    assert AlertConfig().screener_cost_ok_only is False
    raw.pop("screener_cost_ok_only")
    assert AlertConfig(raw).screener_cost_ok_only is False  # an older config copy without the key: off
    assert AlertConfig({**raw, "screener_cost_ok_only": "true"}).screener_cost_ok_only is False  # only a real true


def test_plan_uses_the_screener_cost_rule():
    plan, err = build_plan(screener(TIGHT), cfg())
    assert err is None and not plan["cost_skip"] and not plan["cost_stop_skip"]
    want = cost_as_r(100.0, TIGHT, "ETHUSDT", side="LONG")
    assert plan["screener_cost_r"] == pytest.approx(want["cost_r"]) == pytest.approx(0.0014 * 100 / 0.55)
    assert plan["screener_cost_too_high"] is True and plan["screener_cost_r_max"] == 0.25
    wide, _ = build_plan(screener(WIDE), cfg())
    assert wide["screener_cost_r"] == pytest.approx(0.14) and wide["screener_cost_too_high"] is False
    other, _ = build_plan(screener(TIGHT, source="COMBO_02_V1"), cfg())
    assert other["screener_cost_r"] is None and other["screener_cost_too_high"] is None


def test_flag_off_changes_nothing(tmp_path):
    row = handle(tmp_path, cfg(), screener(TIGHT))
    assert row["code"] == "DRY_RUN" and "cost_r" not in row["flags"]
    assert row["text"].startswith(OBSERVATION_PREFIX)


def test_flag_on_skips_a_screener_alert_whose_cost_is_too_high(tmp_path):
    row = handle(tmp_path, cfg(screener_cost_ok_only=True), screener(TIGHT))
    assert row["kind"] == "SKIP" and row["code"] == SKIPPED_COST_R and row["flags"]["cost_r"] is True
    assert row["text"].startswith(OBSERVATION_PREFIX)  # the preview keeps the UNPROVEN line


def test_flag_on_sends_cost_ok_screener_alerts_with_the_unproven_line(tmp_path):
    row = handle(tmp_path, cfg(screener_cost_ok_only=True), screener(WIDE))
    assert row["code"] == "DRY_RUN" and row["flags"]["cost_r"] is False
    assert row["text"].splitlines()[0] == OBSERVATION_PREFIX


def test_flag_on_leaves_other_sources_alone(tmp_path):
    row = handle(tmp_path, cfg(screener_cost_ok_only=True), screener(TIGHT, source="COMBO_02_V1"))
    assert row["code"] == "DRY_RUN" and "cost_r" not in row["flags"]


def test_entry_falls_back_to_the_signal_close():
    plan, _ = build_plan(screener(TIGHT, entry=None), cfg())
    assert plan["screener_cost_r"] == pytest.approx(cost_as_r(100.0, TIGHT, "ETHUSDT")["cost_r"])


def test_journal_screener_rows_carry_the_entry_price():
    row = {"book": "SCREENER", "event": "signal", "source": "SETUP_SIGNAL_SERVICE", "strategy_id": "SCREENER_SETUP",
           "symbol": "IOSTUSDT", "direction": "LONG", "timeframe": "15m", "setup_bar_time": "2026-10-21T05:00:00+00:00",
           "status": "LONG_ENTRY_CANDIDATE", "outcome": "signal", "stop_price": 0.95, "tp1": 1.12, "tp1_r": 2.4,
           "signal_entry_price": 1.0, "detail_version": 1, "ts": "2026-10-21T05:15:04+00:00"}
    (item,) = list(journal_items([(0, json.dumps(row))], first_line_no=1, data=MemoryData(), cfg=cfg()))
    assert item.extra["signal_entry_price"] == 1.0
