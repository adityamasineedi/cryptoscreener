"""Regression: UI backtest summary metrics must match the trade ledger.

Research-only. Does not change strategy entry/exit/risk rules.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.research.backtest_ui_config import validate_backtest_request
from app.research.metrics import max_drawdown_r, profit_factor
from app.research.trade_fees import enrich_trades

AUDIT_DIR = (
    Path(__file__).resolve().parents[1]
    / "reports"
    / "combo02_v1_reconciliation_audit"
)

DUMPS = [
    "tail_12200.json",
    "db_tail_e3e4c05f.json",
    "ytd_2e654bef.json",
    "feb_apr_31b236ff.json",
]


def _closed(trades: list[dict]) -> list[dict]:
    return [
        t
        for t in trades
        if str(t.get("outcome") or "").upper() not in ("", "OPEN", "NONE")
        and (t.get("exit_time") is not None or t.get("exit_index") is not None)
    ]


@pytest.mark.parametrize("name", DUMPS)
def test_dump_summary_matches_trade_ledger(name: str):
    path = AUDIT_DIR / name
    if not path.is_file():
        pytest.skip(f"missing dump {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    row = data["rows"][0]
    trades = _closed(list(row.get("trades") or []))

    assert row["sample_size"] == len(trades)
    assert len(trades) == len({t["trade_id"] for t in trades})
    assert len(trades) == len({t["entry_index"] for t in trades})
    assert len(trades) == len(
        {(t.get("signal_time"), t.get("exit_time")) for t in trades}
    )

    gross = sum(float(t["gross_pnl_usd"]) for t in trades)
    net = sum(float(t["net_pnl_usd"]) for t in trades)
    fees = sum(float(t["fee_total_usd"]) for t in trades)
    rs = [float(t["r_multiple"]) for t in trades]

    assert abs(gross - float(row["pnl_usd"])) < 1e-6
    assert abs(net - float(row["pnl_usd_net"])) < 1e-6
    assert abs(fees - float(row["fees_usd"])) < 1e-6
    assert abs(gross + fees - net) < 1e-6
    assert abs(sum(rs) / len(rs) - float(row["average_R"])) < 1e-9

    # Chronological engine order = trade list / r_values order.
    # Metrics prepend equity=[0.0] before cumulative R (see metrics.compute_metrics).
    rs_ordered = [float(t["r_multiple"]) for t in trades]
    assert rs_ordered == [float(x) for x in (row.get("r_values") or [])]
    equity = [0.0]
    for r in rs_ordered:
        equity.append(equity[-1] + r)
    dd, _ = max_drawdown_r(equity)
    assert dd is not None
    assert abs(dd - float(row["max_drawdown_R"])) < 1e-9
    pf = profit_factor(rs)
    assert pf is not None
    assert abs(pf - float(row["profit_factor"])) < 1e-9

    # Gross vs net labeling contract used by the UI.
    assert float(row["pnl_usd"]) != float(row["pnl_usd_net"]) or abs(fees) < 1e-12
    assert float(row["pnl_usd"]) == pytest.approx(gross)
    assert float(row["pnl_usd_net"]) == pytest.approx(net)

    # Forming HTF (not fully closed) on every closed trade for COMBO_02_V1.
    for t in trades:
        snap = t.get("condition_snapshot") or {}
        assert snap.get("htf_require_fully_closed") is False


def test_enrich_trades_summary_identity_synthetic():
    """Synthetic closed trades: sum(gross)/sum(net)/fees must stay consistent."""
    raw = [
        {
            "direction": "LONG",
            "entry_price": 100.0,
            "exit_price": 102.0,
            "stop_price": 99.0,
            "quantity": 10.0,
            "r_multiple": 2.0,
            "outcome": "TP1",
            "entry_type": "MARKET",
            "signal_time": "2026-01-01T00:00:00+00:00",
            "exit_time": "2026-01-01T04:00:00+00:00",
            "entry_index": 10,
            "exit_index": 14,
            "trade_id": "a",
        },
        {
            "direction": "LONG",
            "entry_price": 100.0,
            "exit_price": 99.0,
            "stop_price": 99.0,
            "quantity": 10.0,
            "r_multiple": -1.0,
            "outcome": "SL",
            "entry_type": "MARKET",
            "signal_time": "2026-01-02T00:00:00+00:00",
            "exit_time": "2026-01-02T02:00:00+00:00",
            "entry_index": 20,
            "exit_index": 22,
            "trade_id": "b",
        },
        {
            "direction": "LONG",
            "entry_price": 100.0,
            "exit_price": None,
            "stop_price": 99.0,
            "quantity": 10.0,
            "r_multiple": None,
            "outcome": "OPEN",
            "entry_type": "MARKET",
            "signal_time": "2026-01-03T00:00:00+00:00",
            "exit_time": None,
            "entry_index": 30,
            "exit_index": None,
            "trade_id": "open",
        },
    ]
    enriched = enrich_trades(
        raw,
        risk_usd=20.0,
        taker_fee=0.0004,
        maker_fee=0.0002,
        leverage=2.0,
        account_equity=1000.0,
        closed_only=True,
    )
    assert len(enriched) == 2  # OPEN excluded from summary enrichment
    gross = sum(float(t["gross_pnl_usd"]) for t in enriched)
    net = sum(float(t["net_pnl_usd"]) for t in enriched)
    fees = sum(float(t["fee_total_usd"]) for t in enriched)
    assert abs(gross + fees - net) < 1e-6
    assert fees < 0  # negative-cost convention


def test_pnl_usd_is_gross_before_fees_contract():
    """Documented contract: pnl_usd = gross, pnl_usd_net = after fees."""
    path = AUDIT_DIR / "tail_12200.json"
    if not path.is_file():
        pytest.skip("missing 33-trade dump")
    row = json.loads(path.read_text(encoding="utf-8"))["rows"][0]
    assert abs(float(row["pnl_usd"]) - 274.04524879606265) < 1e-6
    assert abs(float(row["pnl_usd_net"]) - 231.23944384606267) < 1e-6
    assert float(row["pnl_usd"]) > float(row["pnl_usd_net"])
    assert abs(
        float(row["pnl_usd"]) + float(row["fees_usd"]) - float(row["pnl_usd_net"])
    ) < 1e-6


def test_configuration_fingerprints_for_three_runs():
    """Fingerprint identity for the three audited UI runs (dates only differ)."""
    common = dict(
        symbols=["BTCUSDT"],
        timeframes=["1h"],
        direction="LONG",
        combination_id="COMBO_02",
        risk_mode="V1_PRODUCTION_PROFILE",
        risk_usd=20,
        principal_usd=1000,
        leverage=2,
        taker_fee_pct=0.04,
        maker_fee_pct=0.02,
    )
    db_tail = validate_backtest_request(**common, start_date=None, end_date=None)
    ytd = validate_backtest_request(
        **common, start_date="2026-01-01", end_date="2026-10-05"
    )
    feb_apr = validate_backtest_request(
        **common, start_date="2026-02-01", end_date="2026-04-30"
    )
    assert db_tail.configuration_fingerprint == "e3e4c05faf4454ec"
    assert ytd.configuration_fingerprint == "2e654bef298db484"
    assert feb_apr.configuration_fingerprint == "31b236ff74e99ba6"
    assert db_tail.period_mode == "DB_TAIL"
    assert ytd.period_mode == "CALENDAR_RANGE"
    assert feb_apr.period_mode == "CALENDAR_RANGE"


def test_db_tail_fingerprint_ignores_limit_by_design():
    """Documented gap: same fingerprint for different DB-tail bar counts."""
    a = validate_backtest_request(
        symbols=["BTCUSDT"],
        timeframes=["1h"],
        direction="LONG",
        combination_id="COMBO_02",
        risk_mode="V1_PRODUCTION_PROFILE",
        risk_usd=20,
        principal_usd=1000,
        leverage=2,
        taker_fee_pct=0.04,
        maker_fee_pct=0.02,
        start_date=None,
        end_date=None,
    )
    assert a.configuration_fingerprint == "e3e4c05faf4454ec"
    # limit is not an input to validate_backtest_request / fingerprint payload.
    assert "limit" not in (
        a.configuration_fingerprint,
        str(a.period_mode),
    )


def test_htf_coverage_formula_for_1h_setup():
    """4h load uses limit//4 + 100 for 1h setups (current service formula)."""

    def lim_4h(limit: int, tf: str = "1h") -> int:
        return max(int(limit), 200) if tf == "4h" else max(int(limit) // 4 + 100, 200)

    assert lim_4h(8640) == 2260
    assert lim_4h(12200) == 3150
    assert lim_4h(200, "4h") == 200
