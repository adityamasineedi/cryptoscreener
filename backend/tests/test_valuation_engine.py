from __future__ import annotations

from app.engines.valuation.engine import METHODS, ValuationEngine, resolve_fdv
from app.models.schemas import DataStatus, FreshValue


def test_volume_mcap_and_mcap_fdv():
    eng = ValuationEngine()
    out = eng.compute(
        market_cap=FreshValue.live(1_000_000_000, "coingecko"),
        fdv=FreshValue.live(2_000_000_000, "coingecko"),
        quote_volume_24h=FreshValue.live(50_000_000, "binance"),
    )
    assert out["volume_mcap"].status == DataStatus.LIVE
    assert abs(out["volume_mcap"].value - 0.05) < 1e-9
    assert out["volume_mcap"].methodology == METHODS["volume_mcap"]
    assert abs(out["mcap_fdv"].value - 0.5) < 1e-9


def test_volume_mcap_inherits_stale_input_status():
    eng = ValuationEngine()
    stale_vol = FreshValue(
        value=50_000_000,
        timestamp=None,
        source="binance",
        status=DataStatus.STALE,
    )
    out = eng.compute(
        market_cap=FreshValue.live(1_000_000_000, "coingecko"),
        quote_volume_24h=stale_vol,
    )
    assert out["volume_mcap"].status == DataStatus.STALE


def test_fdv_from_max_supply_when_provider_equals_mcap():
    price = FreshValue.live(100.0, "binance_ws")
    mcap = FreshValue.live(1_900_000_000_000.0, "coingecko")  # ~19M circ
    fdv_same = FreshValue.live(1_900_000_000_000.0, "coingecko")
    circ = FreshValue.live(19_000_000.0, "coingecko")
    mx = FreshValue.live(21_000_000.0, "coingecko")
    resolved = resolve_fdv(
        fdv=fdv_same,
        max_supply=mx,
        price=price,
        market_cap=mcap,
        circulating_supply=circ,
    )
    assert resolved is not None
    assert resolved.source == "calc"
    assert abs(resolved.value - 21_000_000 * 100.0) < 1e-6
    assert "max_supply" in (resolved.methodology or "")

    out = ValuationEngine().compute(
        market_cap=mcap,
        fdv=fdv_same,
        max_supply=mx,
        circulating_supply=circ,
        price=price,
        quote_volume_24h=FreshValue.live(1e9, "binance"),
    )
    assert abs(out["fdv"].value - 2_100_000_000.0) < 1e-3  # 21M × $100


def test_nvt_waiting_without_onchain():
    eng = ValuationEngine()
    out = eng.compute(
        market_cap=FreshValue.live(1e9, "coingecko"),
        quote_volume_24h=FreshValue.live(1e7, "binance"),
    )
    assert out["nvt"].status == DataStatus.WAITING
    assert "on-chain" in (out["nvt"].methodology or "").lower()
    assert out["nvt_proxy"].status == DataStatus.LIVE
    assert "EXPERIMENTAL" in (out["nvt_proxy"].methodology or "")


def test_true_nvt_when_onchain_present():
    eng = ValuationEngine()
    out = eng.compute(
        market_cap=FreshValue.live(100.0, "coingecko"),
        on_chain_tx_volume=FreshValue.live(10.0, "onchain"),
    )
    assert out["nvt"].status == DataStatus.LIVE
    assert out["nvt"].value == 10.0
    assert out["velocity"].value == 0.1


def test_missing_inputs_waiting():
    eng = ValuationEngine()
    out = eng.compute()
    assert out["volume_mcap"].status == DataStatus.WAITING
    assert out["mcap_fdv"].status == DataStatus.WAITING
