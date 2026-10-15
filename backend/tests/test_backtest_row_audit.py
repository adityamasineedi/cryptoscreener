"""Matrix row postprocess: net metrics, R bases, and warm-up bar accounting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.research.backtest_cpu_worker import (
    CAUSAL_NET_SUMMARY_KEYS,
    apply_causal_net_summary,
    build_matrix_row_postprocess,
)
from app.research.backtest_ui_config import (
    enrich_row_with_config,
    validate_backtest_request,
)
from app.research.causal_execution import clear_isolated_execution_runs
from app.research.trade_fees import summarize_net_trades

RISK_USD = 20.0
T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _candles(n: int = 24) -> list[dict]:
    return [
        {
            "open_time": (T0 + timedelta(hours=i)).isoformat(),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1.0,
        }
        for i in range(n)
    ]


def _trade(exit_price: float, start_hour: int) -> dict:
    return {
        "direction": "LONG",
        "entry_price": 100.0,
        "stop_price": 95.0,
        "exit_price": exit_price,
        "outcome": "TP1" if exit_price > 100.0 else "SL",
        "signal_time": (T0 + timedelta(hours=start_hour)).isoformat(),
        "exit_time": (T0 + timedelta(hours=start_hour + 1)).isoformat(),
        "condition_snapshot": {"entry_type": "MARKET"},
    }


def _payload(**overrides) -> dict:
    data = {
        "backtest_out": {
            "sample_size": 2,
            "status": "OK",
            "result": {
                "sample_size": 2,
                "average_R": -0.4,
                "profit_factor": 0.2,
            },
            "trades": [_trade(95.0, 5), _trade(101.0, 8)],
        },
        "risk_usd": RISK_USD,
        "taker_fee": 0.0004,
        "maker_fee": 0.0002,
        "leverage": 2.0,
        "principal": 1000.0,
        "include_trades": True,
        "direction": "LONG",
        "combination_id": "COMBO_02",
        "load_meta": {"symbol": "BTCUSDT", "timeframe": "1h"},
        "candles": _candles(),
        "index_start": 4,
        "analytics_on": False,
    }
    data.update(overrides)
    return data


def test_postprocess_reports_net_metrics_and_warmup_bars():
    row = build_matrix_row_postprocess(_payload())

    assert row["bars_loaded"] == 24
    assert row["warmup_bars"] == 4
    assert row["bars_evaluated"] == 20
    assert row["r_definition"] == "REALIZED_RISK"
    # Non-causal row: engine fields are passed through untouched.
    assert "pnl_model" not in row
    assert row["profit_factor"] == 0.2
    assert row["average_R"] == -0.4
    assert row["average_R_usd_basis_net"] * row["sample_size"] * RISK_USD == (
        pytest.approx(row["pnl_usd_net"])
    )
    assert row["equity_curve_usd_net"][-1] == pytest.approx(row["pnl_usd_net"])


def _causal_payload(**overrides) -> dict:
    # Legacy book: loss then win. Causal next-open book: both reach TP1.
    loser = {**_trade(95.0, 5), "entry_index": 5, "tp1": 101.0}
    winner = {**_trade(101.0, 8), "entry_index": 8, "tp1": 101.0}
    out = {
        "sample_size": 2,
        "status": "OK",
        "result": {"sample_size": 2, "average_R": -0.4, "profit_factor": 0.2},
        "trades": [loser, winner],
        "execution_model": "DUAL_LEGACY_AND_CAUSAL",
    }
    return _payload(backtest_out=out, combination_id="COMBO_02_V2", **overrides)


@pytest.mark.parametrize("include_trades", [True, False])
def test_causal_swap_recomputes_net_curve_from_causal_trades(include_trades):
    clear_isolated_execution_runs()
    row = build_matrix_row_postprocess(_causal_payload(include_trades=include_trades))
    clear_isolated_execution_runs()

    assert row["pnl_model"] == "CAUSAL_NEXT_OPEN"
    assert row["trades"] == (row["causal_trades"] if include_trades else [])
    causal = summarize_net_trades(row["causal_trades"], risk_usd=RISK_USD)
    legacy = summarize_net_trades(row["legacy_trades"], risk_usd=RISK_USD)
    assert len(causal["equity_curve_usd_net"]) == 2
    for key in CAUSAL_NET_SUMMARY_KEYS:
        assert row[key] == causal[key], key
        assert row[key] != legacy[key], key
    # Causal book never loses; the legacy book opens with a stop-out.
    assert row["equity_curve_usd_net"][0] > 0
    assert row["max_drawdown_usd_net"] == 0
    assert row["win_rate_net"] == 1.0
    assert row["profit_factor_net"] is None
    assert legacy["equity_curve_usd_net"][0] < 0
    assert row["equity_curve_usd_net"][-1] == pytest.approx(row["pnl_usd_net"])

    # Fields owned by apply_causal_primary_display are not touched.
    summary = row["execution_run"]["causal_summary"]
    assert row["sample_size"] == summary["trades"] == 2
    assert row["average_R_net"] == summary["net_r"] / summary["trades"]
    # Gross average R follows the causal book, not the engine result.
    assert row["average_R"] == summary["gross_r"] / summary["trades"]
    assert row["average_R"] != -0.4
    assert row["average_R"] > 0
    # Profit factor follows the causal book: two winners and no losses.
    assert row["profit_factor"] is None
    assert row["pnl_usd"] == summary["gross_pnl"]
    assert row["pnl_usd_net"] == summary["net_pnl"]
    assert row["fees_usd"] == summary["fees"]
    assert row["max_drawdown_R"] == summary["max_drawdown_r"]
    assert row["win_rate"] == summary["win_rate"]


def test_causal_net_summary_helper_replaces_only_the_six_fields():
    row = build_matrix_row_postprocess(_payload())
    before = dict(row)
    row["causal_trades"] = [
        {"r_net": 1.0, "gross_pnl_usd": 21.0, "net_pnl_usd": 20.0},
        {"r_net": -0.5, "gross_pnl_usd": -9.0, "net_pnl_usd": -10.0},
        {"r_net": 1.0, "gross_pnl_usd": 21.0, "net_pnl_usd": 20.0},
    ]
    row["trades"] = []

    apply_causal_net_summary(row, risk_usd=RISK_USD)

    assert row["equity_curve_usd_net"] == [20.0, 10.0, 30.0]
    assert row["max_drawdown_usd_net"] == 10.0
    assert row["profit_factor_net"] == 4.0
    assert row["win_rate_net"] == pytest.approx(2 / 3)
    assert row["average_R_usd_basis_gross"] == pytest.approx(0.55)
    assert row["average_R_usd_basis_net"] == pytest.approx(0.5)
    changed = {k for k in before if k not in ("trades",) and row[k] != before[k]}
    assert changed == set(CAUSAL_NET_SUMMARY_KEYS)


def test_postprocess_without_index_start_has_no_warmup():
    data = _payload()
    del data["index_start"]
    row = build_matrix_row_postprocess(data)
    assert row["warmup_bars"] == 0
    assert row["bars_evaluated"] == 24


def test_enriched_row_uses_evaluated_bars():
    row = build_matrix_row_postprocess(_payload())
    resolved = validate_backtest_request(
        symbols=["BTCUSDT"], timeframes=["1h"], direction="LONG", risk_usd=RISK_USD
    )
    out = enrich_row_with_config(row, resolved)
    assert out["actual_range"]["bars_used"] == 20
    assert out["actual_range"]["warmup_bars"] == 4
    assert out["actual_range"]["bars_loaded"] == 24
    assert out["warmup_bars"] == 4
    assert out["approx_duration_days"] == pytest.approx(0.8)
