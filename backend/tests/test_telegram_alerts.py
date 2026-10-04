"""Telegram AlertFeed subscriber: v1 filter + message formatting."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.services.alerts import AlertFeed
from app.services.telegram_alerts import (
    TelegramAlertSubscriber,
    format_telegram_message,
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
        "quantity": 0.0075,
        "risk_usd": 15.0,
        "timeframe": "1h",
        "signal_snippet": {
            "strategy_id": "COMBO_02_V1",
            "source": "V1_PAPER_WATCHER",
            "path": "A",
            "combo_id": "COMBO_02",
            "combo_version": "v1-combo02-long-htf",
            "v1_tier": "core",
            "risk_percent": 0.015,
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
    assert not is_v1_paper_alert(
        {
            "type": "PAPER_ENTRY",
            "symbol": "DOGEUSDT",
            "timeframe": "1h",
            "payload": _v1_open_payload(symbol="DOGEUSDT"),
        }
    )
    assert is_v1_paper_alert(
        {"type": "PAPER_ENTRY", "symbol": "BTCUSDT", "timeframe": "1h", "payload": _v1_open_payload()}
    )
    assert not is_v1_paper_alert({"type": "BOS", "payload": _v1_open_payload()})


def test_format_entry_message():
    alert = {
        "type": "PAPER_ENTRY",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "payload": _v1_open_payload(),
    }
    text = format_telegram_message(alert)
    assert text is not None
    assert "🟢 LONG BTCUSDT 1h" in text
    assert "COMBO_02 v1 — CORE" in text
    assert "Entry: 100,000" in text
    assert "Stop: 98,000" in text
    assert "TP1: 104,000" in text
    assert "Qty: 0.0075 BTC" in text
    assert "(~$750.00)" in text
    assert "Risk: $15.00 (1.5% of $1000.00)" in text
    assert "HTF: 4h=BULLISH, 1h=BULLISH, HTF_ALIGNED" in text
    assert "R: 2.00R" in text


def test_format_entry_message_sol_includes_qty_and_distances():
    alert = {
        "type": "PAPER_ENTRY",
        "symbol": "SOLUSDT",
        "timeframe": "1h",
        "payload": _v1_open_payload(
            symbol="SOLUSDT",
            entry_price=120.09,
            stop_price=118.9719,
            tp1_price=123.36,
            quantity=4.471,
            risk_usd=5.0,
            signal_snippet={
                "strategy_id": "COMBO_02_V1",
                "source": "V1_PAPER_WATCHER",
                "path": "A",
                "combo_id": "COMBO_02",
                "combo_version": "v1-combo02-long-htf",
                "v1_tier": "secondary",
                "risk_percent": 0.005,
                "symbol": "SOLUSDT",
                "timeframe": "1h",
                "trend_4h": "BULLISH",
                "trend_1h": "BULLISH",
                "htf_alignment": "HTF_ALIGNED",
                "telegram_eligible": True,
            },
        ),
    }
    text = format_telegram_message(alert)
    assert text is not None
    assert "Qty: 4.471 SOL" in text
    assert "Stop: 118.9719 (−1.1181 / −0.93%)" in text
    assert "TP1: 123.36 (+3.27 / +2.72%)" in text
    assert "Risk: $5.00 (0.5% of $1000.00)" in text


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
    assert text.startswith("🔴 CLOSE ETHUSDT 1h")
    assert "Exit: 98,000.00 (SL)" in text
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
                    "path": "PATH_B",
                    "combo_version": "experimental-path-b",
                },
            }

    class PathA:
        def to_dict(self):
            return _v1_open_payload()

    feed.observe_paper_open(PathB())
    feed.observe_paper_open(PathA())

    await asyncio.sleep(0.05)
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    body = requests[0].read()
    assert b"BTCUSDT" in body
    assert b"COMBO_02" in body

    # Idempotent: same trade_id must not send again
    feed.observe_paper_open(PathA())
    await asyncio.sleep(0.05)
    assert len(httpx_mock.get_requests()) == 1

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
        assert st["monitors"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    finally:
        mod._active_subscriber = prev

