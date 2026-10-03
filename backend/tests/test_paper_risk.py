"""Paper risk gates — cap allowlist, volume, book limits, liq spike."""

from __future__ import annotations

from app.models.schemas import DataStatus, FreshValue
from app.services.paper_risk import (
    PaperRiskPolicy,
    evaluate_paper_entry_risk,
)
from app.services.paper_trade import PaperTradeEngine


def test_blocks_small_cap_when_mcap_known(monkeypatch):
    import app.services.paper_risk as pr
    import app.services.engine_store as es

    store = es.EngineStore()
    store.set_fundamentals(
        "MEMEUSDT",
        {
            "market_cap": FreshValue.live(80_000_000.0, "coingecko"),
        },
    )
    monkeypatch.setattr(es, "engine_store", store)
    monkeypatch.setattr(pr, "_quote_volume_24h", lambda _s: (50_000_000.0, "LIVE"))

    gate = evaluate_paper_entry_risk(
        "MEMEUSDT",
        policy=PaperRiskPolicy(),
        open_count=0,
        open_risk_usd=0.0,
        equity=1000.0,
    )
    assert gate.ok is False
    assert "group_not_allowed" in gate.reason
    assert gate.group == "small-cap"


def test_allows_large_cap_with_volume(monkeypatch):
    import app.services.paper_risk as pr
    import app.services.engine_store as es

    store = es.EngineStore()
    store.set_fundamentals(
        "AAAUSDT",
        {"market_cap": FreshValue.live(12_000_000_000.0, "coingecko")},
    )
    monkeypatch.setattr(es, "engine_store", store)
    monkeypatch.setattr(pr, "_quote_volume_24h", lambda _s: (80_000_000.0, "LIVE"))
    monkeypatch.setattr(pr, "_long_liq_spike_blocks", lambda *_a, **_k: (False, ""))

    gate = evaluate_paper_entry_risk(
        "AAAUSDT",
        policy=PaperRiskPolicy(),
        open_count=0,
        open_risk_usd=0.0,
        equity=1000.0,
    )
    assert gate.ok is True
    assert gate.group == "large-cap"
    assert gate.risk_percent == 0.02


def test_mid_cap_uses_lower_risk(monkeypatch):
    import app.services.paper_risk as pr
    import app.services.engine_store as es

    store = es.EngineStore()
    store.set_fundamentals(
        "MIDUSDT",
        {"market_cap": FreshValue.live(2_000_000_000.0, "coingecko")},
    )
    monkeypatch.setattr(es, "engine_store", store)
    monkeypatch.setattr(pr, "_quote_volume_24h", lambda _s: (30_000_000.0, "LIVE"))
    monkeypatch.setattr(pr, "_long_liq_spike_blocks", lambda *_a, **_k: (False, ""))

    gate = evaluate_paper_entry_risk(
        "MIDUSDT",
        policy=PaperRiskPolicy(),
        open_count=0,
        open_risk_usd=0.0,
        equity=1000.0,
    )
    assert gate.ok is True
    assert gate.group == "mid-cap"
    assert gate.risk_percent == 0.01


def test_blocks_when_mcap_waiting_for_non_major(monkeypatch):
    import app.services.paper_risk as pr
    import app.services.engine_store as es

    store = es.EngineStore()
    store.set_fundamentals(
        "XYZUSDT",
        {"market_cap": FreshValue.waiting("coingecko")},
    )
    monkeypatch.setattr(es, "engine_store", store)
    monkeypatch.setattr(pr, "_quote_volume_24h", lambda _s: (100_000_000.0, "LIVE"))

    gate = evaluate_paper_entry_risk(
        "XYZUSDT",
        policy=PaperRiskPolicy(),
        open_count=0,
        open_risk_usd=0.0,
        equity=1000.0,
    )
    assert gate.ok is False
    assert "mcap_unavailable" in gate.reason


def test_btc_allowed_without_mcap(monkeypatch):
    import app.services.paper_risk as pr
    import app.services.engine_store as es

    store = es.EngineStore()
    monkeypatch.setattr(es, "engine_store", store)
    monkeypatch.setattr(pr, "_quote_volume_24h", lambda _s: (None, "WAITING"))
    monkeypatch.setattr(pr, "_long_liq_spike_blocks", lambda *_a, **_k: (False, ""))

    gate = evaluate_paper_entry_risk(
        "BTCUSDT",
        policy=PaperRiskPolicy(),
        open_count=0,
        open_risk_usd=0.0,
        equity=1000.0,
    )
    assert gate.ok is True
    assert gate.group == "BTC"


def test_book_limit_blocks(monkeypatch):
    import app.services.paper_risk as pr

    monkeypatch.setattr(pr, "_mcap_and_group", lambda _s: ("BTC", None, "WAITING"))
    monkeypatch.setattr(pr, "_quote_volume_24h", lambda _s: (1e9, "LIVE"))
    monkeypatch.setattr(pr, "_long_liq_spike_blocks", lambda *_a, **_k: (False, ""))

    gate = evaluate_paper_entry_risk(
        "BTCUSDT",
        policy=PaperRiskPolicy(max_open_positions=2),
        open_count=2,
        open_risk_usd=0.0,
        equity=1000.0,
    )
    assert gate.ok is False
    assert "max_open_positions" in gate.reason


def test_engine_skips_meme_path_a(monkeypatch):
    import app.services.paper_trade as paper_mod
    import app.services.paper_risk as pr
    import app.services.engine_store as es

    store = es.EngineStore()
    store.set_fundamentals(
        "MEMEUSDT",
        {"market_cap": FreshValue.live(40_000_000.0, "coingecko")},
    )
    monkeypatch.setattr(es, "engine_store", store)
    monkeypatch.setattr(pr, "_quote_volume_24h", lambda _s: (9_000_000.0, "LIVE"))
    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 1.0)

    eng = PaperTradeEngine(
        entry_mode="path_a",
        risk_policy=PaperRiskPolicy(enabled=True),
    )
    pos = eng.on_setup_signal(
        "MEMEUSDT",
        {
            "status": "WAITING",
            "direction": "LONG",
            "timeframe": "15m",
            "trend": {"15m": {"trend": "BULLISH"}},
            "bos": {
                "state": "CONFIRMED",
                "direction": "BULLISH_BOS",
                "broken_level": 1.0,
            },
            "stop": {"final_stop": 0.95},
            "targets": [{"target_price": 1.1}],
            "risk_reward": {"RISK_REWARD": "PASS"},
            "source_candle_timestamps": {"15m": "ts-1"},
            "ohlcv_freshness": "OK",
        },
    )
    assert pos is None
    assert eng.status()["last_skip_reason"]
    assert "group_not_allowed" in (eng.status()["last_skip_reason"] or "")


def test_liq_spike_blocks_long(monkeypatch):
    import app.services.paper_risk as pr

    class _Liq:
        def status(self):
            return {"liquidation_status": "LIVE"}

        def get_summary(self, _sym):
            return FreshValue.live("spike", "binance_force_order")

        def aggregates(self, _sym):
            return {
                "5m": {
                    "long_liq_notional": 80_000.0,
                    "short_liq_notional": 10_000.0,
                    "total_notional": 90_000.0,
                },
                "15m": {"total_notional": 30_000.0},  # baseline 10k → 9x spike
            }

    class _Orch:
        liquidations = _Liq()

    monkeypatch.setattr(
        "app.engines.orchestrator.get_orchestrator", lambda: _Orch()
    )
    monkeypatch.setattr(pr, "_mcap_and_group", lambda _s: ("large-cap", 20e9, "LIVE"))
    monkeypatch.setattr(pr, "_quote_volume_24h", lambda _s: (50e6, "LIVE"))

    gate = evaluate_paper_entry_risk(
        "BIGUSDT",
        policy=PaperRiskPolicy(),
        open_count=0,
        open_risk_usd=0.0,
        equity=1000.0,
    )
    assert gate.ok is False
    assert "long_liq_spike" in gate.reason
