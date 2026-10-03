"""Alert feed: transition-only emits, hydrate seeds silently."""

from __future__ import annotations

from app.services.alerts import AlertFeed


def test_setup_hydrate_seeds_without_emit():
    feed = AlertFeed()
    feed.observe_setup_signal(
        "BTCUSDT",
        {
            "status": "NO_SETUP",
            "signal_status": "LIVE",
            "bos": {"state": "CONFIRMED", "direction": "BULLISH_BOS"},
            "market_signal": "BUY",
            "timeframe": "15m",
            "calculated_at": "t0",
        },
    )
    assert feed.list()["count"] == 0


def test_bos_transition_emits_once():
    feed = AlertFeed()
    feed.observe_setup_signal(
        "ETHUSDT",
        {
            "status": "NO_SETUP",
            "signal_status": "LIVE",
            "bos": {"state": "NONE", "direction": ""},
            "market_signal": "NEUTRAL",
            "timeframe": "15m",
            "calculated_at": "t0",
        },
    )
    emitted = feed.observe_setup_signal(
        "ETHUSDT",
        {
            "status": "NO_SETUP",
            "signal_status": "LIVE",
            "bos": {"state": "CONFIRMED", "direction": "BULLISH_BOS", "broken_level": 100},
            "market_signal": "BUY",
            "timeframe": "15m",
            "calculated_at": "t1",
        },
    )
    types = {a["type"] for a in emitted}
    assert "BOS" in types
    assert "MARKET_SIGNAL" in types
    # Same state again — no spam
    again = feed.observe_setup_signal(
        "ETHUSDT",
        {
            "status": "NO_SETUP",
            "signal_status": "LIVE",
            "bos": {"state": "CONFIRMED", "direction": "BULLISH_BOS", "broken_level": 100},
            "market_signal": "BUY",
            "timeframe": "15m",
            "calculated_at": "t1",
        },
    )
    assert again == []


def test_waiting_placeholder_ignored():
    feed = AlertFeed()
    feed.observe_setup_signal(
        "SOLUSDT",
        {
            "status": "NO_SETUP",
            "signal_status": "LIVE",
            "bos": {"state": "NONE"},
            "market_signal": "NEUTRAL",
            "calculated_at": "t0",
        },
    )
    feed.observe_setup_signal(
        "SOLUSDT",
        {"status": "WAITING", "signal_status": "WAITING"},
    )
    emitted = feed.observe_setup_signal(
        "SOLUSDT",
        {
            "status": "LONG_ENTRY_CANDIDATE",
            "signal_status": "LIVE",
            "bos": {"state": "CONFIRMED", "direction": "BULLISH_BOS"},
            "market_signal": "BUY",
            "calculated_at": "t2",
        },
    )
    assert any(a["type"] == "SETUP_STATUS" for a in emitted)


def test_hydrate_snapshot_restores_feed():
    feed = AlertFeed()
    feed.emit(
        alert_type="BOS",
        symbol="BTCUSDT",
        title="test",
        severity="action",
        dedupe_key="k1",
    )
    snap = feed.snapshot_state()
    feed2 = AlertFeed()
    n = feed2.hydrate_from_snapshot(snap)
    assert n == 1
    assert feed2.list(limit=10)["count"] == 1
    assert feed2.list(limit=10)["latest_seq"] >= 1
    # Second hydrate into non-empty feed is skipped
    assert feed2.hydrate_from_snapshot(snap) == 0


def test_paper_and_liq():
    feed = AlertFeed()

    class Pos:
        def to_dict(self):
            return {
                "id": "p1",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry_price": 100,
                "stop_price": 98,
                "tp1_price": 104,
                "timeframe": "15m",
                "signal_snippet": {"path": "PATH_A"},
                "exit_reason": "TP1",
                "pnl_usd": 12.5,
                "r_multiple": 1.5,
            }

    assert feed.observe_paper_open(Pos()) is not None
    assert feed.observe_paper_close(Pos()) is not None
    # Micro notionals must not alert
    assert (
        feed.observe_liquidation_spike(
            "BTCUSDT", is_spike=True, side="SELL", notional_5m=500
        )
        is None
    )
    assert (
        feed.observe_liquidation_spike(
            "BTCUSDT", is_spike=True, side="SELL", notional_5m=1_000_000
        )
        is not None
    )
    # Edge: still spiked — no re-emit
    assert (
        feed.observe_liquidation_spike(
            "BTCUSDT", is_spike=True, notional_5m=2_000_000
        )
        is None
    )
    feed.observe_liquidation_spike("BTCUSDT", is_spike=False, notional_5m=0)
    assert (
        feed.observe_liquidation_spike(
            "BTCUSDT", is_spike=True, side="BUY", notional_5m=50_000
        )
        is not None
    )


def test_sentiment_dt_helper_parses_iso():
    from app.services.persistence import PersistenceService

    dt = PersistenceService._as_utc_dt("2026-10-02T19:07:47.986243+00:00")
    assert dt is not None
    assert dt.tzinfo is not None
    assert PersistenceService._as_utc_dt(None) is None
