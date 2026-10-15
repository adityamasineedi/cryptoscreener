from __future__ import annotations

import csv
import json

from app.research.cross_sectional_discovery import load_cross_sectional_discovery


def test_load_cross_sectional_discovery_returns_typed_read_only_payload(tmp_path):
    (tmp_path / "run_manifest.json").write_text(
        json.dumps({"status": "OK", "candidates_tested": 528}),
        encoding="utf-8",
    )
    with (tmp_path / "selected_holdout.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "candidate_id",
                "lookback",
                "inverse_volatility",
                "holdout_total_return",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "candidate_id": "momentum_lb336",
                "lookback": "336",
                "inverse_volatility": "False",
                "holdout_total_return": "0.53",
            }
        )

    payload = load_cross_sectional_discovery(tmp_path)

    assert payload["status"] == "OK"
    assert payload["read_only"] is True
    assert payload["execution_rights"] is False
    assert payload["paper_trade_created"] is False
    assert payload["manifest"]["candidates_tested"] == 528
    assert payload["selected"] == [
        {
            "candidate_id": "momentum_lb336",
            "lookback": 336,
            "inverse_volatility": False,
            "holdout_total_return": 0.53,
        }
    ]


def test_load_cross_sectional_discovery_reports_missing_artifacts(tmp_path):
    payload = load_cross_sectional_discovery(tmp_path)

    assert payload["status"] == "EMPTY"
    assert payload["selected"] == []
    assert "run_manifest.json" in payload["note"]
    assert "selected_holdout.csv" in payload["note"]


def test_load_cross_sectional_discovery_includes_daily_signal_artifacts(tmp_path):
    (tmp_path / "run_manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "selected_holdout.csv").write_text(
        "candidate_id,holdout_sharpe\nbase,0.2\n", encoding="utf-8"
    )
    (tmp_path / "latest_daily_signals.json").write_text(
        json.dumps({"validation_status": "REJECTED", "signals": []}),
        encoding="utf-8",
    )
    (tmp_path / "daily_signal_manifest.json").write_text(
        json.dumps({"decision_frequency": "daily"}), encoding="utf-8"
    )
    (tmp_path / "daily_signal_holdout.csv").write_text(
        "candidate_id,holdout_total_return\ndaily,-0.15\n", encoding="utf-8"
    )

    payload = load_cross_sectional_discovery(tmp_path)

    assert payload["daily_signals"]["latest"]["validation_status"] == "REJECTED"
    assert payload["daily_signals"]["manifest"]["decision_frequency"] == "daily"
    assert payload["daily_signals"]["holdout"][0]["holdout_total_return"] == -0.15


def test_load_cross_sectional_discovery_includes_discrete_trade_artifacts(tmp_path):
    (tmp_path / "run_manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "selected_holdout.csv").write_text(
        "candidate_id,holdout_sharpe\nbase,0.2\n", encoding="utf-8"
    )
    (tmp_path / "latest_trade_signals.json").write_text(
        json.dumps(
            {
                "validation_status": "PASS",
                "trade_status": "SIGNALS_AVAILABLE",
                "signals": [{"symbol": "ETHUSDT", "action": "BUY"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "trade_signal_manifest.json").write_text(
        json.dumps({"execution_policy": "next hourly open"}), encoding="utf-8"
    )

    payload = load_cross_sectional_discovery(tmp_path)

    assert payload["trade_signals"]["latest"]["validation_status"] == "PASS"
    assert payload["trade_signals"]["latest"]["signals"][0]["action"] == "BUY"
    assert payload["trade_signals"]["manifest"]["execution_policy"] == "next hourly open"


def test_load_cross_sectional_discovery_includes_breakout_risk_artifacts(tmp_path):
    (tmp_path / "run_manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "selected_holdout.csv").write_text(
        "candidate_id,holdout_sharpe\nbase,0.2\n", encoding="utf-8"
    )
    (tmp_path / "latest_breakout_risk.json").write_text(
        json.dumps({"direction_validated": True, "signals": []}), encoding="utf-8"
    )
    (tmp_path / "breakout_risk_manifest.json").write_text(
        json.dumps({"candidates_tested": 276}), encoding="utf-8"
    )

    payload = load_cross_sectional_discovery(tmp_path)

    assert payload["breakout_risk"]["latest"]["direction_validated"] is True
    assert payload["breakout_risk"]["manifest"]["candidates_tested"] == 276
