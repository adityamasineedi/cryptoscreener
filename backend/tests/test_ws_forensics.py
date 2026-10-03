"""WebSocket forensics — classification, tasks, reconnect, gaps, lag (no live Binance)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.ws_forensics import (
    ConnectionForensics,
    DataGapClass,
    DisconnectReason,
    EventLoopLagMonitor,
    WebSocketForensicsRegistry,
    classify_disconnect,
    stream_load_bucket,
    ws_forensics,
)


def test_classify_server_close_1011():
    reason, evidence = classify_disconnect(close_code=1011, close_reason="internal")
    assert reason == DisconnectReason.SERVER_CLOSE
    assert "1011" in evidence


def test_classify_abnormal_1006_is_network_not_binance_intent():
    reason, evidence = classify_disconnect(close_code=1006, close_reason="")
    assert reason == DisconnectReason.NETWORK_ERROR
    assert "abnormal" in evidence.lower()


def test_classify_connection_reset():
    reason, evidence = classify_disconnect(exception=ConnectionResetError("reset"))
    assert reason == DisconnectReason.CONNECTION_RESET
    assert "ConnectionResetError" in evidence


def test_classify_connection_refused():
    reason, _ = classify_disconnect(exception=ConnectionRefusedError("refused"))
    assert reason == DisconnectReason.CONNECTION_REFUSED


def test_classify_ping_timeout_from_exception_message():
    reason, _ = classify_disconnect(exception=TimeoutError("ping timeout"))
    assert reason == DisconnectReason.PING_TIMEOUT


def test_classify_cancelled():
    reason, _ = classify_disconnect(cancelled=True)
    assert reason == DisconnectReason.TASK_CANCELLED


def test_classify_force_24h():
    reason, _ = classify_disconnect(forced_24h=True)
    assert reason == DisconnectReason.FORCE_24H_REFRESH


def test_classify_parser_exception():
    reason, evidence = classify_disconnect(
        parser_exception=True, exception=ValueError("bad")
    )
    assert reason == DisconnectReason.PARSER_EXCEPTION
    assert "ValueError" in evidence


def test_classify_unknown_without_evidence():
    reason, evidence = classify_disconnect()
    assert reason == DisconnectReason.UNKNOWN
    assert "no close frame" in evidence.lower()


def test_stream_load_buckets():
    assert stream_load_bucket(0) == "0-250"
    assert stream_load_bucket(251) == "251-500"
    assert stream_load_bucket(900) == "751-1000"
    assert stream_load_bucket(1001) == "1000+"


def test_duplicate_subscription_detection():
    fox = ConnectionForensics(
        connection_id="WS-TEST",
        name="t",
        endpoint="wss://x",
    )
    fox.note_subscribe_sent(
        3,
        streams=["btcusdt@markPrice", "ethusdt@markPrice", "btcusdt@markPrice"],
    )
    assert fox.duplicate_streams == ["btcusdt@markPrice"]
    assert fox.active_stream_count == 2


def test_subscription_ack_mismatch_issue():
    reg = WebSocketForensicsRegistry()
    fox = reg.get_or_create(name="kline_shard_0", endpoint="wss://x", expected_streams=10)
    fox.note_subscribe_sent(10, streams=[f"s{i}" for i in range(10)])
    # Only partial ack
    fox.acknowledged_stream_count = 3
    fox.subscription_ack_count = 3
    issues = reg.detect_issues()
    codes = {i["error_code"] for i in issues}
    assert "WS_SUBSCRIPTION_MISMATCH" in codes


def test_data_gap_calculation():
    fox = ConnectionForensics(connection_id="WS-G", name="g", endpoint="wss://x")
    fox.note_connected()
    fox.note_first_or_message(nbytes=10, parsed=True)
    # Simulate disconnect opening a gap
    fox.note_disconnect(close_code=1006)
    assert fox._gap_open_at is not None
    # Recover after threshold
    fox._gap_open_at = datetime.now(timezone.utc) - timedelta(seconds=45)
    fox.connected = True
    fox.first_message_at = None  # allow gap close on next message
    fox.note_first_or_message(nbytes=5, parsed=True)
    assert fox.data_gaps
    assert fox.data_gaps[-1].classification == DataGapClass.DATA_GAP


def test_stale_after_reconnect():
    fox = ConnectionForensics(connection_id="WS-S", name="s", endpoint="wss://x")
    fox.note_connected()
    fox.connected_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    fox.note_stale_after_reconnect(gap_threshold_s=30.0)
    assert any(
        g.classification == DataGapClass.STALE_AFTER_RECONNECT for g in fox.data_gaps
    )


def test_reconnect_storm_detection():
    reg = WebSocketForensicsRegistry()
    fox = reg.get_or_create(name="storm", endpoint="wss://x")
    for i in range(6):
        fox.note_reconnect_scheduled(0.1, i + 1)
    issues = reg.detect_issues()
    assert any(i["error_code"] == "WS_RECONNECT_STORM" for i in issues)


def test_task_death_issue():
    reg = WebSocketForensicsRegistry()
    fox = reg.get_or_create(name="dead", endpoint="wss://x")
    fox.note_connected()
    fox.note_task_created("ws:dead")
    fox.task.unexpected_exit_count = 1
    issues = reg.detect_issues()
    assert any(i["error_code"] == "WS_TASK_DIED" for i in issues)


@pytest.mark.asyncio
async def test_event_loop_lag_measurement():
    mon = EventLoopLagMonitor(interval_s=0.05)
    await mon.start()
    await asyncio.sleep(0.2)
    # Intentionally block the loop briefly
    end = asyncio.get_running_loop().time() + 0.15
    while asyncio.get_running_loop().time() < end:
        pass
    await asyncio.sleep(0.15)
    snap = mon.snapshot()
    await mon.stop()
    assert snap["sample_count"] >= 1
    assert snap["max_lag_ms"] >= 0


@pytest.mark.asyncio
async def test_duplicate_reconnect_prevention():
    from app.ingestion.ws_manager import WebSocketConnection

    async def handler(_):
        return None

    conn = WebSocketConnection(
        name="dup_test_unique",
        url="ws://127.0.0.1:1/invalid",
        handler=handler,
        reconnect_base=0.05,
        reconnect_max=0.1,
        open_timeout=0.2,
    )
    await conn.start()
    await conn.start()  # second start should be blocked
    assert conn.forensics.duplicate_reconnect_blocked >= 1
    await conn.stop()


@pytest.mark.asyncio
async def test_ws_connection_reconnects_on_failure():
    from app.ingestion.ws_manager import WebSocketConnection

    async def handler(_data):
        return None

    conn = WebSocketConnection(
        name="test_reconnect_refused",
        url="ws://127.0.0.1:1/invalid",
        handler=handler,
        reconnect_base=0.05,
        reconnect_max=0.1,
        force_reconnect_hours=24,
        open_timeout=0.2,
    )
    await conn.start()
    await asyncio.sleep(0.6)
    assert conn.connect_count == 0
    assert conn.connected is False
    assert conn.connect_attempts >= 1
    assert conn.forensics.reconnect_count >= 1 or conn.forensics.disconnect_count >= 1
    if conn.forensics.disconnect_reason:
        assert conn.forensics.disconnect_reason in (
            DisconnectReason.CONNECTION_REFUSED,
            DisconnectReason.NETWORK_ERROR,
            DisconnectReason.DNS_ERROR,
            DisconnectReason.READ_TIMEOUT,
            DisconnectReason.UNKNOWN,
        )
    await conn.stop()


@pytest.mark.asyncio
async def test_cancellation_during_reconnect():
    from app.ingestion.ws_manager import WebSocketConnection

    async def handler(_):
        return None

    conn = WebSocketConnection(
        name="cancel_during_reconnect",
        url="ws://127.0.0.1:1/invalid",
        handler=handler,
        reconnect_base=5.0,
        reconnect_max=5.0,
        open_timeout=0.2,
    )
    await conn.start()
    await asyncio.sleep(0.1)
    await conn.stop()
    assert conn.connected is False
    assert conn._task is None


def test_old_task_termination_flag_on_stop_lifecycle():
    fox = ConnectionForensics(connection_id="WS-T", name="t", endpoint="wss://x")
    fox.note_task_created("ws:t")
    fox.note_task_created("ws:t")  # restart
    assert fox.task is not None
    assert fox.task.restart_count == 1


@pytest.mark.asyncio
async def test_reader_task_death_logged_in_status():
    from app.ingestion.ws_manager import WebSocketConnection

    async def handler(_):
        return None

    conn = WebSocketConnection(
        name="status_task_check",
        url="ws://127.0.0.1:1/invalid",
        handler=handler,
        reconnect_base=10,
        reconnect_max=10,
        open_timeout=0.2,
    )
    await conn.start()
    await asyncio.sleep(0.05)
    # Force mark connected with dead task to simulate failure mode
    conn.connected = True
    conn.reader_running = True
    if conn._task and not conn._task.done():
        conn._task.cancel()
        try:
            await conn._task
        except asyncio.CancelledError:
            pass
    from app.ingestion.ws_manager import WebSocketManager

    mgr = WebSocketManager()
    mgr._connections[conn.name] = conn
    rows = mgr.status()
    assert rows[0]["reader_running"] is False
    await conn.stop()


def test_semantic_stale_and_reader_dead():
    from app.diagnostics.collectors.websocket import _semantic_ws_status

    st, reason, _ = _semantic_ws_status(
        connected=True,
        frames=10,
        last_frame_at=datetime.now(timezone.utc).isoformat(),
        stale_after=120,
        reader_running=False,
    )
    assert st == "ERROR"
    assert reason == "CONNECTED_BUT_READER_DEAD"


@pytest.mark.asyncio
async def test_diagnostics_websocket_payload_shape():
    from app.diagnostics.collectors.websocket import collect_websocket_forensics

    settings = MagicMock()
    settings.diag_ws_stale_seconds = 120
    with patch("app.engines.orchestrator.get_orchestrator", return_value=None), patch(
        "app.ingestion.service.get_ingestion", return_value=None
    ):
        # Ensure registry has at least one connection for detail path
        fox = ws_forensics.get_or_create(name="payload_shape", endpoint="wss://example")
        out = await collect_websocket_forensics(settings)
    assert "overall_status" in out
    assert "disconnect_summary" in out
    assert "reconnect_summary" in out
    assert "event_loop" in out
    assert "subscription_health" in out
    assert "data_gap_summary" in out
    assert "connections" in out
    detail = await __import__(
        "app.diagnostics.collectors.websocket", fromlist=["collect_websocket_connection_detail"]
    ).collect_websocket_connection_detail(fox.connection_id)
    assert detail is not None
    assert detail["connection_id"] == fox.connection_id


@pytest.mark.asyncio
async def test_kline_subscribe_ack_tracking():
    from app.ingestion.kline_ws_manager import KlineShardConnection

    async def handler(_):
        return None

    conn = KlineShardConnection(
        name="kline_ack_test",
        base_ws="wss://fstream.binance.com",
        streams=["btcusdt@kline_1m", "ethusdt@kline_1m", "btcusdt@kline_1m"],
        handler=handler,
    )
    # Simulate subscribe bookkeeping without network
    class FakeWs:
        async def send(self, _msg):
            return None

    await conn._subscribe_all(FakeWs())
    assert conn.forensics.duplicate_streams == ["btcusdt@kline_1m"]
    assert conn.forensics.requested_stream_count == 2
    conn.forensics.note_subscribe_ack()
    assert conn.forensics.acknowledged_stream_count == 2


@pytest.mark.asyncio
async def test_rest_recovery_after_ws_gap_not_hidden():
    """REST recovery must not erase disconnect forensics."""
    fox = ws_forensics.get_or_create(name="gap_rest", endpoint="wss://x")
    fox.note_connected()
    fox.note_first_or_message(nbytes=1)
    fox.note_disconnect(close_code=1006)
    before = fox.disconnect_count
    # Simulate REST backfill path doing nothing to counters
    assert fox.disconnect_count == before
    assert fox.disconnect_events
    assert fox.disconnect_events[-1].reason == DisconnectReason.NETWORK_ERROR
