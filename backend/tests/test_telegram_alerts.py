"""Telegram AlertFeed subscriber: v1 filter + message formatting."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.services.alerts import AlertFeed
from app.services.telegram_alerts import (
    TelegramAlertSubscriber,
    format_telegram_message,
    is_telegram_trade_alert,
    is_v1_paper_alert,
    start_telegram_alerts,
)


def _v1_open_payload(**overrides):
    base = {
        "id": "p1",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_price": 100_000.0,
        "stop_price": 98_000.0,
        "tp1_price": 104_000.0,
        "quantity": 0.01,
        "risk_usd": 20.0,
        "timeframe": "1h",
        "signal_snippet": {
            "strategy_id": "COMBO_02_V1",
            "source": "V1_PAPER_WATCHER",
            "path": "A",
            "combo_id": "COMBO_02",
            "combo_version": "v1-combo02-long-htf",
            "v1_tier": "core",
            "risk_percent": 0.02,
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "trend_4h": "BULLISH",
            "trend_1h": "BULLISH",
            "htf_alignment": "HTF_ALIGNED",
            "telegram_eligible": True,
        },
        "strategy_id": "COMBO_02_V1",
        "source": "V1_PAPER_WATCHER",
        "telegram_eligible": True,
    }
    base.update(overrides)
    return base


def _v1_close_payload(**overrides):
    base = _v1_open_payload(
        exit_price=104_000.0,
        exit_reason="TP1",
        pnl_usd=30.0,
        r_multiple=2.0,
        status="CLOSED",
    )
    base.update(overrides)
    return base


def test_is_v1_paper_alert_strict_1h_path_a():
    assert not is_v1_paper_alert(
        {
            "type": "PAPER_ENTRY",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "payload": {
                "timeframe": "15m",
                "signal_snippet": {
                    "path": "A",
                    "combo_id": "COMBO_02",
                    "combo_version": "v1-combo02-long-htf",
                },
            },
        }
    )
    # Outside the v1 paper/Telegram universe — still blocked.
    obscure = _v1_open_payload(symbol="BATUSDT")
    obscure["signal_snippet"] = {
        **obscure["signal_snippet"],
        "symbol": "BATUSDT",
        "v1_tier": "secondary",
    }
    assert not is_v1_paper_alert(
        {
            "type": "PAPER_ENTRY",
            "symbol": "BATUSDT",
            "timeframe": "1h",
            "payload": obscure,
        }
    )
    assert is_v1_paper_alert(
        {"type": "PAPER_ENTRY", "symbol": "BTCUSDT", "timeframe": "1h", "payload": _v1_open_payload()}
    )
    # Extended v1 paper majors (e.g. DOGE/ADA) must reach Telegram when fully gated.
    doge = _v1_open_payload(symbol="DOGEUSDT")
    doge["signal_snippet"] = {
        **doge["signal_snippet"],
        "symbol": "DOGEUSDT",
        "v1_tier": "secondary",
    }
    assert is_v1_paper_alert(
        {
            "type": "PAPER_ENTRY",
            "symbol": "DOGEUSDT",
            "timeframe": "1h",
            "payload": doge,
        }
    )
    assert not is_v1_paper_alert({"type": "BOS", "payload": _v1_open_payload()})


def test_telegram_trade_alert_allows_legacy_eligible_coin():
    """Any paper-eligible coin with entry/stop can send (not only v1 books)."""
    alert = {
        "type": "PAPER_ENTRY",
        "symbol": "1000PEPEUSDT",
        "timeframe": "15m",
        "payload": {
            "id": "pepe1",
            "symbol": "1000PEPEUSDT",
            "side": "LONG",
            "entry_price": 0.00438,
            "stop_price": 0.0042,
            "tp1_price": 0.00442,
            "timeframe": "15m",
            "signal_snippet": {
                "strategy_id": "RESEARCH_15M",
                "source": "LEGACY_SETUP_SIGNAL",
                "path": "LEGACY",
                "telegram_eligible": False,
            },
        },
    }
    assert not is_v1_paper_alert(alert)
    assert is_telegram_trade_alert(alert)
    text = format_telegram_message(alert)
    assert text is not None
    assert "SCALP TRADE - 1000PEPE" in text


def test_telegram_trade_alert_blocks_path_b_and_short_research():
    assert not is_telegram_trade_alert(
        {
            "type": "PAPER_ENTRY",
            "symbol": "BTCUSDT",
            "payload": {
                "entry_price": 100.0,
                "stop_price": 98.0,
                "signal_snippet": {
                    "strategy_id": "EXPERIMENTAL_PATH_B",
                    "source": "LEGACY_SETUP_SIGNAL",
                    "path": "B",
                },
            },
        }
    )
    assert not is_telegram_trade_alert(
        {
            "type": "PAPER_ENTRY",
            "symbol": "BTCUSDT",
            "payload": {
                "entry_price": 100.0,
                "stop_price": 98.0,
                "side": "SHORT",
                "signal_snippet": {
                    "strategy_id": "COMBO_02_SHORT_RESEARCH",
                    "source": "SHORT_RESEARCH_PIPELINE",
                    "direction": "SHORT",
                },
            },
        }
    )


def test_format_entry_message():
    alert = {
        "type": "PAPER_ENTRY",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "payload": _v1_open_payload(),
    }
    text = format_telegram_message(alert)
    assert text is not None
    assert text.startswith("SCALP TRADE - BTC")
    assert "🏮 TYPE - LONG" in text
    assert "👉 ENTRY - $100,000" in text
    assert "👉 TARGET - " in text
    assert "$104,000" in text
    assert "👉 SL - $98,000" in text
    assert "🚨LEVERAGE - 2x" in text


def test_format_entry_message_sol_scalp_layout():
    alert = {
        "type": "PAPER_ENTRY",
        "symbol": "SOLUSDT",
        "timeframe": "1h",
        "payload": _v1_open_payload(
            symbol="SOLUSDT",
            entry_price=120.09,
            stop_price=118.9719,
            tp1_price=123.36,
            quantity=17.884,
            risk_usd=20.0,
            signal_snippet={
                "strategy_id": "COMBO_02_V1",
                "source": "V1_PAPER_WATCHER",
                "path": "A",
                "combo_id": "COMBO_02",
                "combo_version": "v1-combo02-long-htf",
                "v1_tier": "secondary",
                "risk_percent": 0.02,
                "symbol": "SOLUSDT",
                "timeframe": "1h",
                "trend_4h": "BULLISH",
                "trend_1h": "BULLISH",
                "htf_alignment": "HTF_ALIGNED",
                "telegram_eligible": True,
                "paper_execution": {"leverage": 2.0},
            },
        ),
    }
    text = format_telegram_message(alert)
    assert text is not None
    assert "SCALP TRADE - SOL" in text
    assert "👉 ENTRY - $120.09" in text
    assert "👉 SL - $118.9719" in text
    assert "$123.36" in text
    assert "🚨LEVERAGE - 2x" in text


def test_format_exit_maps_stop_to_sl():
    alert = {
        "type": "PAPER_EXIT",
        "symbol": "ETHUSDT",
        "timeframe": "1h",
        "payload": _v1_close_payload(
            symbol="ETHUSDT",
            exit_price=98_000.0,
            exit_reason="STOP",
            pnl_usd=-15.0,
            r_multiple=-1.0,
            quantity=0.15,
        ),
    }
    text = format_telegram_message(alert)
    assert text is not None
    assert text.startswith("CLOSE TRADE - ETH")
    assert "👉 EXIT - $98,000.00 (SL)" in text
    assert "Qty: 0.15 ETH" in text
    assert "R: -1.00R" in text
    assert "PnL: $-15.00" in text
    assert "Hold: n/a bars" in text


def test_start_telegram_alerts_disabled_by_default_flag():
    settings = MagicMock()
    settings.paper_v1_telegram_enabled = False
    settings.telegram_bot_token = "tok"
    settings.telegram_chat_id = "123"
    assert start_telegram_alerts(settings) is None


def test_start_telegram_alerts_disabled_without_creds():
    settings = MagicMock()
    settings.paper_v1_telegram_enabled = True
    settings.telegram_bot_token = None
    settings.telegram_chat_id = None
    assert start_telegram_alerts(settings) is None


@pytest.mark.asyncio
async def test_subscriber_sends_only_v1_via_feed(httpx_mock):
    httpx_mock.add_response(
        url="https://api.telegram.org/botTESTTOKEN/sendMessage",
        method="POST",
        json={"ok": True, "result": {}},
        is_reusable=True,
    )
    feed = AlertFeed()
    sub = TelegramAlertSubscriber(
        bot_token="TESTTOKEN",
        chat_id="12345",
        feed=feed,
    )
    assert sub.start()

    class PathB:
        def to_dict(self):
            return {
                "id": "b1",
                "symbol": "DOGEUSDT",
                "side": "LONG",
                "entry_price": 1,
                "stop_price": 0.9,
                "tp1_price": 1.2,
                "timeframe": "15m",
                "signal_snippet": {
                    "strategy_id": "EXPERIMENTAL_PATH_B",
                    "source": "LEGACY_SETUP_SIGNAL",
                    "path": "PATH_B",
                    "combo_version": "experimental-path-b",
                },
            }

    class PathA:
        def to_dict(self):
            return _v1_open_payload()

    class LegacyPepe:
        def to_dict(self):
            return {
                "id": "pepe1",
                "symbol": "1000PEPEUSDT",
                "side": "LONG",
                "entry_price": 0.00438,
                "stop_price": 0.0042,
                "tp1_price": 0.00442,
                "timeframe": "15m",
                "signal_snippet": {
                    "strategy_id": "RESEARCH_15M",
                    "source": "LEGACY_SETUP_SIGNAL",
                    "path": "LEGACY",
                    "telegram_eligible": False,
                },
            }

    feed.observe_paper_open(PathB())
    feed.observe_paper_open(PathA())
    feed.observe_paper_open(LegacyPepe())

    await asyncio.sleep(0.05)
    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    bodies = b" ".join(r.read() for r in requests)
    assert b"SCALP TRADE - BTC" in bodies
    assert b"SCALP TRADE - 1000PEPE" in bodies
    assert b"LEVERAGE" in bodies

    # Idempotent: same trade_id must not send again
    feed.observe_paper_open(PathA())
    await asyncio.sleep(0.05)
    assert len(httpx_mock.get_requests()) == 2

    sub.stop()
    await asyncio.sleep(0)


def test_telegram_delivery_status_no_secrets():
    from types import SimpleNamespace

    from app.services import telegram_alerts as mod

    prev = mod._active_subscriber
    mod._active_subscriber = None
    try:
        st = mod.telegram_delivery_status(
            SimpleNamespace(
                paper_v1_telegram_enabled=True,
                telegram_bot_token="SECRET_TOKEN",
                telegram_chat_id="999001",
            )
        )
        assert st["enabled"] is True
        assert st["configured"] is True
        assert st["subscribed"] is False
        assert st["ready"] is False
        assert "SECRET_TOKEN" not in str(st)
        from app.research.v1_production import V1_SYMBOLS

        assert st["monitors"] == sorted(V1_SYMBOLS)
        assert "ADAUSDT" in st["monitors"]
        assert "BTCUSDT" in st["monitors"]
    finally:
        mod._active_subscriber = prev

