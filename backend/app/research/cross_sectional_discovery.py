"""Read-only adapter for cross-sectional perpetual-futures research artifacts."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_DIR = (
    Path(__file__).resolve().parents[3]
    / "research_sandbox"
    / "cross_sectional_perps_20261009"
    / "outputs"
)

_INTEGER_FIELDS = {
    "lookback",
    "rebalance_hours",
    "sides",
    "holdout_liquidation_warning_bars",
}
_BOOLEAN_FIELDS = {"inverse_volatility", "beta_neutral"}


def _typed_value(key: str, value: str) -> Any:
    if value == "":
        return None
    if key in _BOOLEAN_FIELDS:
        return value.strip().lower() == "true"
    if key in _INTEGER_FIELDS:
        return int(value)
    if key in {"candidate_id", "family"}:
        return value
    try:
        return float(value)
    except ValueError:
        return value


def load_cross_sectional_discovery(
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Load the latest fixed-location artifacts without granting execution rights."""
    directory = (output_dir or DEFAULT_OUTPUT_DIR).resolve()
    manifest_path = directory / "run_manifest.json"
    selected_path = directory / "selected_holdout.csv"
    timestamp = datetime.now(timezone.utc).isoformat()

    missing = [path.name for path in (manifest_path, selected_path) if not path.is_file()]
    if missing:
        return {
            "status": "EMPTY",
            "research_only": True,
            "read_only": True,
            "execution_rights": False,
            "selected": [],
            "note": f"Missing research artifacts: {', '.join(missing)}",
            "timestamp": timestamp,
        }

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        with selected_path.open("r", encoding="utf-8", newline="") as handle:
            selected = [
                {key: _typed_value(key, value) for key, value in row.items()}
                for row in csv.DictReader(handle)
            ]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "ERROR",
            "research_only": True,
            "read_only": True,
            "execution_rights": False,
            "selected": [],
            "error": f"Could not read cross-sectional research artifacts: {exc}",
            "timestamp": timestamp,
        }

    generated_at = datetime.fromtimestamp(
        max(manifest_path.stat().st_mtime, selected_path.stat().st_mtime),
        tz=timezone.utc,
    ).isoformat()
    daily: dict[str, Any] | None = None
    daily_signals_path = directory / "latest_daily_signals.json"
    daily_manifest_path = directory / "daily_signal_manifest.json"
    daily_holdout_path = directory / "daily_signal_holdout.csv"
    if daily_signals_path.is_file() and daily_manifest_path.is_file():
        daily = {
            "latest": json.loads(daily_signals_path.read_text(encoding="utf-8")),
            "manifest": json.loads(daily_manifest_path.read_text(encoding="utf-8")),
            "holdout": [],
        }
        if daily_holdout_path.is_file():
            with daily_holdout_path.open("r", encoding="utf-8", newline="") as handle:
                daily["holdout"] = [
                    {key: _typed_value(key, value) for key, value in row.items()}
                    for row in csv.DictReader(handle)
                ]

    trade_signals: dict[str, Any] | None = None
    trade_signals_path = directory / "latest_trade_signals.json"
    trade_manifest_path = directory / "trade_signal_manifest.json"
    if trade_signals_path.is_file() and trade_manifest_path.is_file():
        trade_signals = {
            "latest": json.loads(trade_signals_path.read_text(encoding="utf-8")),
            "manifest": json.loads(trade_manifest_path.read_text(encoding="utf-8")),
        }

    breakout_risk: dict[str, Any] | None = None
    breakout_path = directory / "latest_breakout_risk.json"
    breakout_manifest_path = directory / "breakout_risk_manifest.json"
    if breakout_path.is_file() and breakout_manifest_path.is_file():
        breakout_risk = {
            "latest": json.loads(breakout_path.read_text(encoding="utf-8")),
            "manifest": json.loads(breakout_manifest_path.read_text(encoding="utf-8")),
        }

    return {
        "status": "OK",
        "research_only": True,
        "read_only": True,
        "execution_rights": False,
        "paper_trade_created": False,
        "live_trade_created": False,
        "telegram_sent": False,
        "generated_at_utc": generated_at,
        "manifest": manifest,
        "selected": selected,
        "trade_signals": trade_signals,
        "breakout_risk": breakout_risk,
        "daily_signals": daily,
        "artifacts": {
            "manifest": manifest_path.name,
            "selected_holdout": selected_path.name,
        },
        "timestamp": timestamp,
    }
