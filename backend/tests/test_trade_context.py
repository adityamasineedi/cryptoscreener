"""Per-trade market context labels — display only, closed bars only."""

from datetime import datetime, timedelta, timezone

from app.research.trade_context import attach_trade_context, structure_verdict


def _candles(n=120, *, volume=100.0):
    t0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        px = 100.0 + i * 0.5
        out.append(
            {
                "time": t0 + timedelta(hours=i),
                "open": px,
                "high": px + 1.0,
                "low": px - 1.0,
                "close": px + 0.4,
                "volume": volume,
            }
        )
    return out


def test_structure_verdict_follows_trade_side():
    assert structure_verdict("LONG", "BULLISH") == "CORRECT"
    assert structure_verdict("LONG", "BEARISH") == "WRONG"
    assert structure_verdict("SHORT", "BEARISH") == "CORRECT"
    assert structure_verdict("SHORT", "BULLISH") == "WRONG"
    assert structure_verdict("LONG", "NEUTRAL") == "NEUTRAL"
    assert structure_verdict("LONG", None) == "NEUTRAL"


def test_context_reads_the_signal_bar_and_never_a_later_one():
    candles = _candles()
    candles[60]["volume"] = 10.0  # a tenth of its 20-bar average
    trade = {"direction": "LONG", "signal_time": candles[60]["time"].isoformat(), "net_pnl_usd": 5.0}
    assert attach_trade_context([trade], candles, timeframe="1h", symbol="TESTUSDT") == 1
    ctx = trade["market_context"]
    assert ctx["decision_bar"] == candles[60]["time"].isoformat()
    assert ctx["volume"] == "contraction"
    assert ctx["relative_volume"] == 0.1
    # Steady rise: the close sits above the prior 24-bar range, so the upper half.
    assert ctx["zone"] == "HIGH"
    assert "Low volume" in ctx["label"] and "High zone" in ctx["label"]
    assert trade["net_pnl_usd"] == 5.0

    # Changing bars after the signal bar must not change its labels.
    later = [dict(c) for c in candles]
    for c in later[61:]:
        c["volume"], c["high"], c["low"], c["close"] = 9999.0, 1.0, 0.5, 0.7
    again = {"direction": "LONG", "signal_time": candles[60]["time"].isoformat()}
    attach_trade_context([again], later, timeframe="1h", symbol="TESTUSDT")
    assert again["market_context"] == ctx


def test_offset_uses_the_bar_before_an_open_stamped_entry():
    candles = _candles()
    trade = {"direction": "SHORT", "signal_time": candles[60]["time"].isoformat()}
    attach_trade_context([trade], candles, timeframe="1h", symbol="TESTUSDT", decision_offset=-1)
    assert trade["market_context"]["decision_bar"] == candles[59]["time"].isoformat()


def test_unknown_time_and_empty_inputs_are_skipped():
    candles = _candles()
    trade = {"direction": "LONG", "signal_time": "2020-01-01T00:00:00+00:00"}
    assert attach_trade_context([trade], candles, timeframe="1h", symbol="TESTUSDT") == 0
    assert "market_context" not in trade
    assert attach_trade_context([], candles, timeframe="1h", symbol="TESTUSDT") == 0
    assert attach_trade_context([trade], [], timeframe="1h", symbol="TESTUSDT") == 0
