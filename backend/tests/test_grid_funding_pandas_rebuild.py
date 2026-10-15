"""Independent pandas rebuild from a small recorded Binance funding page."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest


FIXTURE = Path(__file__).parent / "fixtures" / "grid_funding_pandas_rebuild.json"


def test_recorded_funding_page_rebuilds_exact_payment_with_pandas():
    recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))

    candles = pd.DataFrame(recorded["candles"]).sort_values("time")
    fills = pd.DataFrame(recorded["fills"])
    fills["delta"] = fills["qty"].where(fills["side"].eq("buy"), -fills["qty"])
    deltas = fills.groupby("time", as_index=True)["delta"].sum()
    candles["closing_qty"] = candles["time"].map(deltas).fillna(0.0).cumsum()
    candles["opening_qty"] = candles["closing_qty"].shift(fill_value=0.0)
    candles["bar_time"] = pd.to_datetime(candles["time"], unit="s", utc=True)

    settlements = pd.DataFrame(recorded["settlements"])
    snapped_seconds = (settlements["fundingTime"] / 1000.0).round().astype("int64")
    settlements["settlement_time"] = pd.to_datetime(snapped_seconds, unit="s", utc=True)
    settlements["rate"] = pd.to_numeric(settlements["fundingRate"])
    settlements["mark"] = pd.to_numeric(settlements["markPrice"], errors="coerce")
    rebuilt = pd.merge_asof(
        settlements.sort_values("settlement_time"),
        candles.sort_values("bar_time"),
        left_on="settlement_time",
        right_on="bar_time",
        direction="backward",
    )

    inside_qty = rebuilt[["opening_qty", "closing_qty"]].max(axis=1)
    inside_qty = inside_qty.where(
        rebuilt["rate"].gt(0),
        rebuilt[["opening_qty", "closing_qty"]].min(axis=1),
    )
    rebuilt["inventory"] = inside_qty.where(
        rebuilt["settlement_time"].ne(rebuilt["bar_time"]),
        rebuilt["opening_qty"],
    )
    rebuilt["price"] = rebuilt["mark"].where(rebuilt["mark"].gt(0), rebuilt["close"])
    rebuilt["payment"] = rebuilt["inventory"] * rebuilt["price"] * rebuilt["rate"]

    assert len(rebuilt) == 2
    assert rebuilt["inventory"].tolist() == pytest.approx([0.25, 0.15])
    assert rebuilt["price"].tolist() == pytest.approx([42000.5, 41900.0])
    assert rebuilt["payment"].sum() == pytest.approx(recorded["expected_funding_paid"])
