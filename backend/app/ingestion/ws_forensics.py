"""WebSocket connection forensics — expose disconnects; never hide them.

Additive diagnostics only. Does not change reconnect policy, stream selection,
or trading/signal behavior.
"""

from __future__ import annotations

import asyncio
import socket
import ssl
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.core.logging import get_logger

logger = get_logger("ws_forensics")


class DisconnectReason(str, Enum):
    SERVER_CLOSE = "SERVER_CLOSE"
    CLIENT_CLOSE = "CLIENT_CLOSE"
    FORCE_24H_REFRESH = "FORCE_24H_REFRESH"
    PING_TIMEOUT = "PING_TIMEOUT"
    PONG_TIMEOUT = "PONG_TIMEOUT"
    READ_TIMEOUT = "READ_TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    DNS_ERROR = "DNS_ERROR"
    TLS_ERROR = "TLS_ERROR"
    CONNECTION_RESET = "CONNECTION_RESET"
    CONNECTION_REFUSED = "CONNECTION_REFUSED"
    TASK_CANCELLED = "TASK_CANCELLED"
    PARSER_EXCEPTION = "PARSER_EXCEPTION"
    CONSUMER_EXCEPTION = "CONSUMER_EXCEPTION"
    SUBSCRIPTION_FAILURE = "SUBSCRIPTION_FAILURE"
    UNKNOWN = "UNKNOWN"


class DataGapClass(str, Enum):
    NO_DATA_GAP = "NO_DATA_GAP"
    RECOVERED_WITHIN_THRESHOLD = "RECOVERED_WITHIN_THRESHOLD"
    DATA_GAP = "DATA_GAP"
    STALE_AFTER_RECONNECT = "STALE_AFTER_RECONNECT"
    UNKNOWN = "UNKNOWN"


class RootCauseConfidence(str, Enum):
    CONFIRMED = "CONFIRMED"
    STRONG_EVIDENCE = "STRONG_EVIDENCE"
    CORRELATION_ONLY = "CORRELATION_ONLY"
    UNKNOWN = "UNKNOWN"


# Client-initiated close codes commonly used by our stack.
_CLIENT_CLOSE_CODES = {1000, 1001}
# Abnormal / no close frame
_ABNORMAL_CODES = {1006}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def classify_disconnect(
    *,
    close_code: int | None = None,
    close_reason: str | None = None,
    exception: BaseException | None = None,
    forced_24h: bool = False,
    cancelled: bool = False,
    ping_timeout: bool = False,
    pong_timeout: bool = False,
    parser_exception: bool = False,
    consumer_exception: bool = False,
    subscription_failure: bool = False,
) -> tuple[DisconnectReason, str]:
    """Classify disconnect from evidence only. Prefer UNKNOWN over guessing."""
    if cancelled:
        return DisconnectReason.TASK_CANCELLED, "asyncio.CancelledError / task cancelled"
    if forced_24h:
        return (
            DisconnectReason.FORCE_24H_REFRESH,
            "client force reconnect before Binance 24h limit",
        )
    if ping_timeout:
        return DisconnectReason.PING_TIMEOUT, "ping timeout flagged by connection layer"
    if pong_timeout:
        return DisconnectReason.PONG_TIMEOUT, "pong timeout flagged by connection layer"
    if parser_exception:
        evidence = f"{type(exception).__name__}: {exception}" if exception else "parser"
        return DisconnectReason.PARSER_EXCEPTION, evidence
    if consumer_exception:
        evidence = (
            f"{type(exception).__name__}: {exception}" if exception else "consumer"
        )
        return DisconnectReason.CONSUMER_EXCEPTION, evidence
    if subscription_failure:
        evidence = (
            f"{type(exception).__name__}: {exception}"
            if exception
            else "subscription failure"
        )
        return DisconnectReason.SUBSCRIPTION_FAILURE, evidence

    if exception is not None:
        name = type(exception).__name__
        msg = str(exception)
        evidence = f"{name}: {msg}"
        lower = f"{name} {msg}".lower()

        # websockets library timeout on keepalive
        if "ping" in lower and "timeout" in lower:
            return DisconnectReason.PING_TIMEOUT, evidence
        if "pong" in lower and "timeout" in lower:
            return DisconnectReason.PONG_TIMEOUT, evidence
        if "handshake" in lower and ("timed out" in lower or "timeout" in lower):
            return DisconnectReason.NETWORK_ERROR, evidence
        if isinstance(exception, ConnectionRefusedError) or "refused" in lower:
            return DisconnectReason.CONNECTION_REFUSED, evidence
        if isinstance(exception, asyncio.TimeoutError) or "timed out" in lower:
            return DisconnectReason.READ_TIMEOUT, evidence
        if isinstance(exception, ConnectionResetError) or "reset" in lower:
            return DisconnectReason.CONNECTION_RESET, evidence
        if isinstance(exception, (socket.gaierror, OSError)) and (
            "getaddrinfo" in lower or "name or service not known" in lower or "dns" in lower
        ):
            return DisconnectReason.DNS_ERROR, evidence
        if isinstance(exception, (ssl.SSLError,)) or "ssl" in lower or "tls" in lower:
            return DisconnectReason.TLS_ERROR, evidence
        if isinstance(
            exception,
            (OSError, ConnectionError, TimeoutError),
        ) or any(
            k in lower
            for k in (
                "network",
                "unreachable",
                "broken pipe",
                "eof",
                "connection closed",
            )
        ):
            # ConnectionClosed handled below via close_code path when available
            if "connectionclosed" in name.lower():
                pass
            else:
                return DisconnectReason.NETWORK_ERROR, evidence

        # ConnectionClosed from websockets exposes .code / .reason
        code = getattr(exception, "code", close_code)
        reason = getattr(exception, "reason", close_reason) or ""
        if code is not None:
            return _classify_close_code(int(code), str(reason), evidence)

    if close_code is not None:
        return _classify_close_code(
            int(close_code),
            close_reason or "",
            f"websocket close frame code={close_code} reason={close_reason!r}",
        )

    return DisconnectReason.UNKNOWN, "no close frame or exception evidence"


def _classify_close_code(
    code: int, reason: str, evidence: str
) -> tuple[DisconnectReason, str]:
    # 1000/1001 from our force-refresh path are CLIENT_CLOSE / FORCE handled earlier.
    # Without more context, 1000/1001 could be either side — mark SERVER_CLOSE only
    # when reason/evidence implies remote; otherwise UNKNOWN for 1000 alone is safer.
    if code in _ABNORMAL_CODES:
        return (
            DisconnectReason.NETWORK_ERROR,
            f"{evidence}; 1006=abnormal closure (no close frame) — not proof of intentional server close",
        )
    if code >= 4000:
        # Binance / app-specific
        return DisconnectReason.SERVER_CLOSE, evidence
    if code in (1011, 1012, 1013, 1014):
        return DisconnectReason.SERVER_CLOSE, evidence
    if code == 1001:
        # Going away — often server restart or client navigating away.
        # Without who-initiated evidence keep as SERVER_CLOSE only if reason set;
        # else UNKNOWN.
        if reason:
            return DisconnectReason.SERVER_CLOSE, evidence
        return DisconnectReason.UNKNOWN, evidence
    if code == 1000:
        if reason:
            return DisconnectReason.CLIENT_CLOSE, evidence
        return DisconnectReason.UNKNOWN, evidence
    if 1002 <= code <= 1015:
        return DisconnectReason.SERVER_CLOSE, evidence
    return DisconnectReason.UNKNOWN, evidence


def stream_load_bucket(stream_count: int) -> str:
    if stream_count <= 250:
        return "0-250"
    if stream_count <= 500:
        return "251-500"
    if stream_count <= 750:
        return "501-750"
    if stream_count <= 1000:
        return "751-1000"
    return "1000+"


@dataclass
class TaskForensics:
    task_name: str
    connection_id: str
    created_at: datetime
    restart_count: int = 0
    last_done_at: datetime | None = None
    last_exception: str | None = None
    last_cancelled: bool = False
    unexpected_exit_count: int = 0

    def observe(self, task: asyncio.Task | None, *, expecting_alive: bool) -> dict[str, Any]:
        done = bool(task is None or task.done())
        cancelled = bool(task is not None and task.cancelled())
        exc_s: str | None = None
        if task is not None and task.done() and not task.cancelled():
            try:
                exc = task.exception()
            except asyncio.CancelledError:
                cancelled = True
                exc = None
            except Exception as e:  # noqa: BLE001
                exc = e
            if exc is not None:
                exc_s = f"{type(exc).__name__}: {exc}"
                self.last_exception = exc_s
        self.last_cancelled = cancelled
        if done:
            self.last_done_at = _utcnow()
            if expecting_alive and not cancelled:
                self.unexpected_exit_count += 1
        return {
            "task_name": self.task_name,
            "connection_id": self.connection_id,
            "created_at": _iso(self.created_at),
            "done": done,
            "cancelled": cancelled,
            "exception": exc_s or self.last_exception,
            "restart_count": self.restart_count,
            "unexpected_exit_count": self.unexpected_exit_count,
            "last_done_at": _iso(self.last_done_at),
        }


@dataclass
class DisconnectEvent:
    at: datetime
    reason: DisconnectReason
    evidence: str
    close_code: int | None
    close_reason: str | None
    connection_duration_s: float | None
    stream_count: int
    last_message_age_s: float | None
    reconnect_attempt: int
    raw_messages_before: int
    load_bucket: str


@dataclass
class DataGapEvent:
    at: datetime
    disconnect_duration_s: float | None
    affected_stream_count: int
    last_data_timestamp: datetime | None
    recovery_timestamp: datetime | None
    data_gap_duration_s: float | None
    classification: DataGapClass
    gap_threshold_s: float


@dataclass
class ConnectionForensics:
    connection_id: str
    name: str
    endpoint: str
    market: str = "futures"
    shard_id: str | None = None
    stream_type: str = "unknown"
    created_at: datetime = field(default_factory=_utcnow)
    connected_at: datetime | None = None
    first_message_at: datetime | None = None
    last_message_at: datetime | None = None
    last_ping_at: datetime | None = None
    last_pong_at: datetime | None = None
    closed_at: datetime | None = None
    close_code: int | None = None
    close_reason: str | None = None
    exception_type: str | None = None
    exception_message: str | None = None
    disconnect_reason: DisconnectReason | None = None
    disconnect_evidence: str | None = None
    reconnect_attempt: int = 0
    reconnect_delay: float | None = None
    stream_count: int = 0
    expected_stream_count: int = 0
    requested_stream_count: int = 0
    acknowledged_stream_count: int = 0
    active_stream_count: int = 0
    subscription_count: int = 0
    subscription_ack_count: int = 0
    last_subscribe_sent_at: datetime | None = None
    last_subscribe_ack_at: datetime | None = None
    subscription_ack_latency_ms: float | None = None
    duplicate_streams: list[str] = field(default_factory=list)
    raw_messages: int = 0
    parsed_messages: int = 0
    parse_errors: int = 0
    bytes_received: int = 0
    last_message_type: str | None = None
    last_message_stream: str | None = None
    connect_count: int = 0
    disconnect_count: int = 0
    reconnect_count: int = 0
    connected: bool = False
    reader_running: bool = False
    reconnect_owner_active: bool = False
    duplicate_reconnect_blocked: int = 0
    forced_24h_count: int = 0
    messages_window: deque = field(default_factory=lambda: deque(maxlen=600))
    bytes_window: deque = field(default_factory=lambda: deque(maxlen=600))
    reconnect_times: deque = field(default_factory=lambda: deque(maxlen=500))
    disconnect_events: deque = field(default_factory=lambda: deque(maxlen=100))
    data_gaps: deque = field(default_factory=lambda: deque(maxlen=100))
    lifecycle: deque = field(default_factory=lambda: deque(maxlen=80))
    task: TaskForensics | None = None
    _gap_open_at: datetime | None = None
    _gap_last_data: datetime | None = None
    _gap_stream_count: int = 0
    _rate_anchor: float = field(default_factory=time.monotonic)
    _rate_msgs: int = 0
    _rate_bytes: int = 0
    _rate_parse_errors: int = 0
    messages_per_second: float = 0.0
    bytes_per_second: float = 0.0
    parse_errors_per_second: float = 0.0

    def _life(self, phase: str) -> None:
        stamp = _utcnow().strftime("%H:%M:%S")
        self.lifecycle.append(f"{stamp} {phase}")

    def note_task_created(self, task_name: str) -> None:
        if self.task is None:
            self.task = TaskForensics(
                task_name=task_name,
                connection_id=self.connection_id,
                created_at=_utcnow(),
            )
        else:
            self.task.restart_count += 1
            self.task.task_name = task_name
            self.task.created_at = _utcnow()
        self._life("TASK_CREATED")

    def note_connected(self, *, endpoint: str | None = None) -> None:
        now = _utcnow()
        self.connected = True
        self.reader_running = True
        self.connected_at = now
        self.closed_at = None
        # Per-session first message — LIVE requires data after this connect
        self.first_message_at = None
        self.connect_count += 1
        if endpoint:
            self.endpoint = endpoint
        self.exception_type = None
        self.exception_message = None
        self._life("CONNECTED")

    def note_first_or_message(
        self,
        *,
        nbytes: int = 0,
        message_type: str | None = None,
        stream: str | None = None,
        parsed: bool = True,
        parse_error: bool = False,
    ) -> None:
        now = _utcnow()
        if self.first_message_at is None:
            self.first_message_at = now
            self._life("FIRST_MESSAGE")
            # Close any open gap on first post-reconnect data
            self._close_gap_if_open(now, gap_threshold_s=30.0)
        self.last_message_at = now
        self.raw_messages += 1
        self.bytes_received += max(0, nbytes)
        self._rate_msgs += 1
        self._rate_bytes += max(0, nbytes)
        if parse_error:
            self.parse_errors += 1
            self._rate_parse_errors += 1
        elif parsed:
            self.parsed_messages += 1
        if message_type:
            self.last_message_type = message_type
        if stream:
            self.last_message_stream = stream
        self._refresh_rates()

    def note_subscribe_sent(self, count: int, streams: list[str] | None = None) -> None:
        self.last_subscribe_sent_at = _utcnow()
        self.requested_stream_count = count
        self.subscription_count = count
        if streams is not None:
            seen: set[str] = set()
            dups: list[str] = []
            for s in streams:
                if s in seen:
                    dups.append(s)
                seen.add(s)
            self.duplicate_streams = dups
            self.active_stream_count = len(seen)
            self.stream_count = len(seen)
        self._life(f"SUBSCRIBE_SENT n={count}")

    def note_subscribe_ack(self, *, ack_count: int | None = None) -> None:
        now = _utcnow()
        self.last_subscribe_ack_at = now
        if ack_count is not None:
            self.acknowledged_stream_count = ack_count
            self.subscription_ack_count = ack_count
        else:
            self.subscription_ack_count += 1
            # Binance SUBSCRIBE ack often has result=null; treat as batch ack
            if self.requested_stream_count:
                self.acknowledged_stream_count = self.requested_stream_count
        if self.last_subscribe_sent_at is not None:
            self.subscription_ack_latency_ms = (
                now - self.last_subscribe_sent_at
            ).total_seconds() * 1000.0
        self._life("SUBSCRIBE_ACK")

    def note_reconnect_scheduled(self, delay: float, attempt: int) -> None:
        self.reconnect_delay = delay
        self.reconnect_attempt = attempt
        self.reconnect_count += 1
        self.reconnect_times.append(_utcnow())
        self.reconnect_owner_active = True
        self._life(f"RECONNECT_SCHEDULED delay={delay:.2f} attempt={attempt}")

    def note_reconnect_owner_idle(self) -> None:
        self.reconnect_owner_active = False

    def note_duplicate_reconnect_blocked(self) -> None:
        self.duplicate_reconnect_blocked += 1
        self._life("DUPLICATE_RECONNECT_BLOCKED")

    def note_force_24h(self) -> None:
        self.forced_24h_count += 1
        self._life("FORCE_24H_REFRESH")

    def note_disconnect(
        self,
        *,
        close_code: int | None = None,
        close_reason: str | None = None,
        exception: BaseException | None = None,
        forced_24h: bool = False,
        cancelled: bool = False,
        ping_timeout: bool = False,
        pong_timeout: bool = False,
        parser_exception: bool = False,
        consumer_exception: bool = False,
        subscription_failure: bool = False,
        gap_threshold_s: float = 30.0,
    ) -> DisconnectEvent:
        now = _utcnow()
        was_connected = self.connected
        duration = None
        if self.connected_at is not None:
            duration = (now - self.connected_at).total_seconds()
        last_age = None
        if self.last_message_at is not None:
            last_age = (now - self.last_message_at).total_seconds()

        reason, evidence = classify_disconnect(
            close_code=close_code,
            close_reason=close_reason,
            exception=exception,
            forced_24h=forced_24h,
            cancelled=cancelled,
            ping_timeout=ping_timeout,
            pong_timeout=pong_timeout,
            parser_exception=parser_exception,
            consumer_exception=consumer_exception,
            subscription_failure=subscription_failure,
        )
        self.connected = False
        self.reader_running = False
        self.closed_at = now
        self.close_code = close_code
        self.close_reason = close_reason
        self.disconnect_reason = reason
        self.disconnect_evidence = evidence
        if exception is not None:
            self.exception_type = type(exception).__name__
            self.exception_message = str(exception)[:500]
        if was_connected or close_code is not None or exception is not None:
            self.disconnect_count += 1

        ev = DisconnectEvent(
            at=now,
            reason=reason,
            evidence=evidence,
            close_code=close_code,
            close_reason=close_reason,
            connection_duration_s=duration,
            stream_count=self.stream_count or self.expected_stream_count,
            last_message_age_s=last_age,
            reconnect_attempt=self.reconnect_attempt,
            raw_messages_before=self.raw_messages,
            load_bucket=stream_load_bucket(self.stream_count or self.expected_stream_count),
        )
        self.disconnect_events.append(ev)
        self._life(f"DISCONNECTED {reason.value}")

        # Open data-gap window from last message (or disconnect time)
        if self._gap_open_at is None:
            self._gap_open_at = self.last_message_at or now
            self._gap_last_data = self.last_message_at
            self._gap_stream_count = self.stream_count or self.expected_stream_count
            # If never received data, still record unknown gap class later
            _ = gap_threshold_s

        logger.warning(
            "ws_disconnect_classified",
            connection_id=self.connection_id,
            reason=reason.value,
            evidence=evidence,
            close_code=close_code,
            stream_count=ev.stream_count,
            connection_duration_s=duration,
            last_message_age_s=last_age,
        )
        return ev

    def _close_gap_if_open(self, recovery: datetime, *, gap_threshold_s: float) -> None:
        if self._gap_open_at is None:
            return
        gap_start = self._gap_open_at
        last_data = self._gap_last_data
        duration = (recovery - gap_start).total_seconds()
        if last_data is None:
            classification = DataGapClass.UNKNOWN
            gap_dur = duration
        elif duration <= gap_threshold_s:
            classification = DataGapClass.RECOVERED_WITHIN_THRESHOLD
            gap_dur = duration
        elif duration > gap_threshold_s:
            classification = DataGapClass.DATA_GAP
            gap_dur = duration
        else:
            classification = DataGapClass.NO_DATA_GAP
            gap_dur = 0.0
        self.data_gaps.append(
            DataGapEvent(
                at=gap_start,
                disconnect_duration_s=duration,
                affected_stream_count=self._gap_stream_count,
                last_data_timestamp=last_data,
                recovery_timestamp=recovery,
                data_gap_duration_s=gap_dur,
                classification=classification,
                gap_threshold_s=gap_threshold_s,
            )
        )
        self._gap_open_at = None
        self._gap_last_data = None
        self._gap_stream_count = 0
        self._life(f"GAP_CLOSED {classification.value} dur={duration:.2f}s")

    def note_stale_after_reconnect(self, *, gap_threshold_s: float = 30.0) -> None:
        if self.connected and self.first_message_at is None and self.connected_at:
            age = (_utcnow() - self.connected_at).total_seconds()
            if age > gap_threshold_s:
                self.data_gaps.append(
                    DataGapEvent(
                        at=self.connected_at,
                        disconnect_duration_s=age,
                        affected_stream_count=self.stream_count,
                        last_data_timestamp=self.last_message_at,
                        recovery_timestamp=None,
                        data_gap_duration_s=age,
                        classification=DataGapClass.STALE_AFTER_RECONNECT,
                        gap_threshold_s=gap_threshold_s,
                    )
                )

    def _refresh_rates(self) -> None:
        now = time.monotonic()
        elapsed = now - self._rate_anchor
        if elapsed >= 1.0:
            self.messages_per_second = self._rate_msgs / elapsed
            self.bytes_per_second = self._rate_bytes / elapsed
            self.parse_errors_per_second = self._rate_parse_errors / elapsed
            self.messages_window.append((now, self.messages_per_second))
            self.bytes_window.append((now, self.bytes_per_second))
            self._rate_anchor = now
            self._rate_msgs = 0
            self._rate_bytes = 0
            self._rate_parse_errors = 0

    def reconnects_in(self, window_s: float) -> int:
        cutoff = _utcnow().timestamp() - window_s
        return sum(1 for t in self.reconnect_times if t.timestamp() >= cutoff)

    def uptime_seconds(self) -> float | None:
        if self.connected and self.connected_at is not None:
            return (_utcnow() - self.connected_at).total_seconds()
        return None

    def downtime_seconds(self) -> float | None:
        if not self.connected and self.closed_at is not None:
            return (_utcnow() - self.closed_at).total_seconds()
        return None

    def subscription_health(self) -> dict[str, Any]:
        exp = self.expected_stream_count
        req = self.requested_stream_count
        ack = self.acknowledged_stream_count
        act = self.active_stream_count
        return {
            "expected_stream_count": exp,
            "requested_stream_count": req,
            "acknowledged_stream_count": ack,
            "active_stream_count": act,
            "expected_ne_requested": exp != req if exp and req else None,
            "requested_ne_acknowledged": req != ack if req else None,
            "acknowledged_ne_active": ack != act if ack and act else None,
            "duplicate_streams": list(self.duplicate_streams),
            "subscription_ack_latency_ms": self.subscription_ack_latency_ms,
        }

    def to_summary(self) -> dict[str, Any]:
        self._refresh_rates()
        task_snap = None
        if self.task is not None:
            task_snap = {
                "task_name": self.task.task_name,
                "restart_count": self.task.restart_count,
                "unexpected_exit_count": self.task.unexpected_exit_count,
                "last_exception": self.task.last_exception,
                "last_cancelled": self.task.last_cancelled,
            }
        return {
            "connection_id": self.connection_id,
            "name": self.name,
            "endpoint": self.endpoint,
            "market": self.market,
            "shard_id": self.shard_id,
            "stream_type": self.stream_type,
            "created_at": _iso(self.created_at),
            "connected_at": _iso(self.connected_at),
            "first_message_at": _iso(self.first_message_at),
            "last_message_at": _iso(self.last_message_at),
            "last_ping_at": _iso(self.last_ping_at),
            "last_pong_at": _iso(self.last_pong_at),
            "closed_at": _iso(self.closed_at),
            "close_code": self.close_code,
            "close_reason": self.close_reason,
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "disconnect_reason": self.disconnect_reason.value
            if self.disconnect_reason
            else None,
            "disconnect_evidence": self.disconnect_evidence,
            "reconnect_attempt": self.reconnect_attempt,
            "reconnect_delay": self.reconnect_delay,
            "stream_count": self.stream_count,
            "subscription_count": self.subscription_count,
            "subscription_ack_count": self.subscription_ack_count,
            "raw_messages": self.raw_messages,
            "parsed_messages": self.parsed_messages,
            "parse_errors": self.parse_errors,
            "bytes_received": self.bytes_received,
            "last_message_type": self.last_message_type,
            "last_message_stream": self.last_message_stream,
            "uptime_seconds": self.uptime_seconds(),
            "downtime_seconds": self.downtime_seconds(),
            "connect_count": self.connect_count,
            "disconnect_count": self.disconnect_count,
            "reconnect_count": self.reconnect_count,
            "connected": self.connected,
            "reader_running": self.reader_running,
            "messages_per_second": round(self.messages_per_second, 3),
            "bytes_per_second": round(self.bytes_per_second, 1),
            "parse_errors_per_second": round(self.parse_errors_per_second, 3),
            "load_bucket": stream_load_bucket(self.stream_count or self.expected_stream_count),
            "reconnects_last_1m": self.reconnects_in(60),
            "reconnects_last_5m": self.reconnects_in(300),
            "reconnects_last_1h": self.reconnects_in(3600),
            "duplicate_reconnect_blocked": self.duplicate_reconnect_blocked,
            "forced_24h_count": self.forced_24h_count,
            "subscription_health": self.subscription_health(),
            "task": task_snap,
            "lifecycle": list(self.lifecycle),
            "recent_disconnects": [
                {
                    "at": _iso(e.at),
                    "reason": e.reason.value,
                    "evidence": e.evidence,
                    "close_code": e.close_code,
                    "close_reason": e.close_reason,
                    "connection_duration_s": e.connection_duration_s,
                    "stream_count": e.stream_count,
                    "last_message_age_s": e.last_message_age_s,
                    "load_bucket": e.load_bucket,
                }
                for e in list(self.disconnect_events)[-10:]
            ],
            "recent_data_gaps": [
                {
                    "at": _iso(g.at),
                    "disconnect_duration_s": g.disconnect_duration_s,
                    "affected_stream_count": g.affected_stream_count,
                    "last_data_timestamp": _iso(g.last_data_timestamp),
                    "recovery_timestamp": _iso(g.recovery_timestamp),
                    "data_gap_duration_s": g.data_gap_duration_s,
                    "classification": g.classification.value,
                }
                for g in list(self.data_gaps)[-10:]
            ],
        }

    def to_detail(self) -> dict[str, Any]:
        base = self.to_summary()
        base["all_disconnects"] = [
            {
                "at": _iso(e.at),
                "reason": e.reason.value,
                "evidence": e.evidence,
                "close_code": e.close_code,
                "close_reason": e.close_reason,
                "connection_duration_s": e.connection_duration_s,
                "stream_count": e.stream_count,
                "last_message_age_s": e.last_message_age_s,
                "reconnect_attempt": e.reconnect_attempt,
                "raw_messages_before": e.raw_messages_before,
                "load_bucket": e.load_bucket,
            }
            for e in self.disconnect_events
        ]
        base["all_data_gaps"] = [
            {
                "at": _iso(g.at),
                "disconnect_duration_s": g.disconnect_duration_s,
                "affected_stream_count": g.affected_stream_count,
                "last_data_timestamp": _iso(g.last_data_timestamp),
                "recovery_timestamp": _iso(g.recovery_timestamp),
                "data_gap_duration_s": g.data_gap_duration_s,
                "classification": g.classification.value,
                "gap_threshold_s": g.gap_threshold_s,
            }
            for g in self.data_gaps
        ]
        return base


class EventLoopLagMonitor:
    """Lightweight 1s heartbeat measuring event-loop scheduling lag."""

    def __init__(self, interval_s: float = 1.0) -> None:
        self.interval_s = interval_s
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.samples: deque[float] = deque(maxlen=3600)
        self.max_lag_ms: float = 0.0
        self.over_100ms: int = 0
        self.over_500ms: int = 0
        self.over_1000ms: int = 0
        self.last_lag_ms: float = 0.0
        self.started_at: datetime | None = None
        self.sample_count: int = 0

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self.started_at = _utcnow()
        self._task = asyncio.create_task(self._run(), name="ws_event_loop_lag")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        expected = loop.time() + self.interval_s
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_s)
                break
            except asyncio.TimeoutError:
                pass
            now = loop.time()
            lag_ms = max(0.0, (now - expected) * 1000.0)
            expected = now + self.interval_s
            self.last_lag_ms = lag_ms
            self.samples.append(lag_ms)
            self.sample_count += 1
            if lag_ms > self.max_lag_ms:
                self.max_lag_ms = lag_ms
            if lag_ms > 100:
                self.over_100ms += 1
            if lag_ms > 500:
                self.over_500ms += 1
            if lag_ms > 1000:
                self.over_1000ms += 1
                logger.warning("ws_event_loop_lag", lag_ms=round(lag_ms, 1))

    def _percentile(self, p: float) -> float | None:
        if not self.samples:
            return None
        ordered = sorted(self.samples)
        if len(ordered) == 1:
            return ordered[0]
        idx = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
        return ordered[idx]

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": bool(self._task and not self._task.done()),
            "started_at": _iso(self.started_at),
            "interval_s": self.interval_s,
            "sample_count": self.sample_count,
            "last_lag_ms": round(self.last_lag_ms, 2),
            "max_lag_ms": round(self.max_lag_ms, 2),
            "p95_lag_ms": round(self._percentile(95) or 0.0, 2) if self.samples else None,
            "p99_lag_ms": round(self._percentile(99) or 0.0, 2) if self.samples else None,
            "over_100ms": self.over_100ms,
            "over_500ms": self.over_500ms,
            "over_1000ms": self.over_1000ms,
        }


class WebSocketForensicsRegistry:
    """Global registry of WS connection forensics + issue detection."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._seq = 0
        self._by_name: dict[str, ConnectionForensics] = {}
        self._by_id: dict[str, ConnectionForensics] = {}
        self.event_loop = EventLoopLagMonitor()
        self._orphan_alerts: list[dict[str, Any]] = []

    def allocate_id(self) -> str:
        self._seq += 1
        return f"WS-{self._seq:03d}"

    def get_or_create(
        self,
        *,
        name: str,
        endpoint: str,
        stream_type: str = "unknown",
        market: str = "futures",
        shard_id: str | None = None,
        expected_streams: int = 0,
    ) -> ConnectionForensics:
        existing = self._by_name.get(name)
        if existing is not None:
            existing.endpoint = endpoint or existing.endpoint
            existing.stream_type = stream_type or existing.stream_type
            if expected_streams:
                existing.expected_stream_count = expected_streams
                existing.stream_count = max(existing.stream_count, expected_streams)
            return existing
        cid = self.allocate_id()
        fox = ConnectionForensics(
            connection_id=cid,
            name=name,
            endpoint=endpoint,
            market=market,
            shard_id=shard_id or name,
            stream_type=stream_type,
            expected_stream_count=expected_streams,
            stream_count=expected_streams,
        )
        self._by_name[name] = fox
        self._by_id[cid] = fox
        logger.info(
            "ws_forensics_registered",
            connection_id=cid,
            name=name,
            stream_type=stream_type,
        )
        return fox

    def get(self, connection_id: str) -> ConnectionForensics | None:
        return self._by_id.get(connection_id) or self._by_name.get(connection_id)

    def remove(self, name: str) -> None:
        fox = self._by_name.pop(name, None)
        if fox is not None:
            self._by_id.pop(fox.connection_id, None)

    def all_connections(self) -> list[ConnectionForensics]:
        return list(self._by_name.values())

    def detect_issues(self) -> list[dict[str, Any]]:
        """Evidence-based issue candidates only."""
        issues: list[dict[str, Any]] = []
        now = _utcnow()
        for fox in self.all_connections():
            # Task death while marked connected
            if fox.connected and fox.task and fox.task.unexpected_exit_count > 0:
                issues.append(
                    {
                        "error_code": "WS_TASK_DIED",
                        "severity": "ERROR",
                        "message": f"Reader task died while connected: {fox.connection_id}",
                        "details": {
                            "connection_id": fox.connection_id,
                            "task_name": fox.task.task_name,
                            "exception": fox.task.last_exception,
                            "stream_count": fox.stream_count,
                            "last_message_at": _iso(fox.last_message_at),
                            "probable_root_cause": RootCauseConfidence.STRONG_EVIDENCE.value,
                            "confidence": RootCauseConfidence.STRONG_EVIDENCE.value,
                            "exact_location": "backend/app/ingestion/ws_forensics.py:detect_issues",
                        },
                        "expected": "reader task alive while connected",
                        "actual": f"unexpected_exit_count={fox.task.unexpected_exit_count}",
                    }
                )

            r1 = fox.reconnects_in(60)
            if r1 >= 5:
                issues.append(
                    {
                        "error_code": "WS_RECONNECT_STORM",
                        "severity": "ERROR",
                        "message": f"Reconnect storm: {fox.connection_id} ({r1}/1m)",
                        "details": {
                            "connection_id": fox.connection_id,
                            "reconnects_last_1m": r1,
                            "reconnects_last_5m": fox.reconnects_in(300),
                            "last_disconnect_reason": fox.disconnect_reason.value
                            if fox.disconnect_reason
                            else None,
                            "last_evidence": fox.disconnect_evidence,
                            "probable_root_cause": RootCauseConfidence.CORRELATION_ONLY.value,
                            "confidence": RootCauseConfidence.CORRELATION_ONLY.value,
                            "exact_location": f"{fox.name}",
                        },
                        "expected": "<5 reconnects/min",
                        "actual": str(r1),
                    }
                )

            if fox.disconnect_count >= 10 and fox.reconnects_in(3600) >= 10:
                issues.append(
                    {
                        "error_code": "WS_HIGH_DISCONNECT_RATE",
                        "severity": "WARNING",
                        "message": f"High disconnect rate: {fox.connection_id}",
                        "details": {
                            "connection_id": fox.connection_id,
                            "disconnect_count": fox.disconnect_count,
                            "reconnects_last_1h": fox.reconnects_in(3600),
                            "recent_reasons": [
                                e.reason.value for e in list(fox.disconnect_events)[-5:]
                            ],
                            "probable_root_cause": RootCauseConfidence.CORRELATION_ONLY.value,
                            "confidence": RootCauseConfidence.CORRELATION_ONLY.value,
                            "exact_location": fox.name,
                        },
                        "expected": "stable connection",
                        "actual": f"disconnects={fox.disconnect_count}",
                    }
                )

            if fox.disconnect_reason == DisconnectReason.PING_TIMEOUT:
                issues.append(
                    {
                        "error_code": "WS_PING_TIMEOUT",
                        "severity": "WARNING",
                        "message": f"Ping timeout: {fox.connection_id}",
                        "details": {
                            "connection_id": fox.connection_id,
                            "evidence": fox.disconnect_evidence,
                            "probable_root_cause": RootCauseConfidence.STRONG_EVIDENCE.value
                            if fox.disconnect_evidence
                            else RootCauseConfidence.UNKNOWN.value,
                            "confidence": RootCauseConfidence.STRONG_EVIDENCE.value,
                            "exact_location": fox.name,
                        },
                    }
                )

            sub = fox.subscription_health()
            if sub.get("requested_ne_acknowledged") or sub.get("expected_ne_requested"):
                issues.append(
                    {
                        "error_code": "WS_SUBSCRIPTION_MISMATCH",
                        "severity": "WARNING",
                        "message": f"Subscription mismatch: {fox.connection_id}",
                        "details": {
                            "connection_id": fox.connection_id,
                            **sub,
                            "probable_root_cause": RootCauseConfidence.STRONG_EVIDENCE.value,
                            "confidence": RootCauseConfidence.STRONG_EVIDENCE.value,
                            "exact_location": fox.name,
                        },
                        "expected": str(sub.get("expected_stream_count")),
                        "actual": str(sub.get("acknowledged_stream_count")),
                    }
                )
            if sub.get("duplicate_streams"):
                issues.append(
                    {
                        "error_code": "WS_DUPLICATE_SUBSCRIPTION",
                        "severity": "WARNING",
                        "message": f"Duplicate streams on {fox.connection_id}",
                        "details": {
                            "connection_id": fox.connection_id,
                            "duplicates": sub["duplicate_streams"][:20],
                            "probable_root_cause": RootCauseConfidence.CONFIRMED.value,
                            "confidence": RootCauseConfidence.CONFIRMED.value,
                            "exact_location": fox.name,
                        },
                    }
                )

            for gap in list(fox.data_gaps)[-3:]:
                if gap.classification in (
                    DataGapClass.DATA_GAP,
                    DataGapClass.STALE_AFTER_RECONNECT,
                ):
                    issues.append(
                        {
                            "error_code": "WS_DATA_GAP",
                            "severity": "WARNING",
                            "message": f"Data gap on {fox.connection_id}: {gap.classification.value}",
                            "details": {
                                "connection_id": fox.connection_id,
                                "classification": gap.classification.value,
                                "data_gap_duration_s": gap.data_gap_duration_s,
                                "affected_stream_count": gap.affected_stream_count,
                                "probable_root_cause": RootCauseConfidence.STRONG_EVIDENCE.value,
                                "confidence": RootCauseConfidence.STRONG_EVIDENCE.value,
                                "exact_location": fox.name,
                            },
                            "expected": f"gap <= {gap.gap_threshold_s}s",
                            "actual": str(gap.data_gap_duration_s),
                        }
                    )

            # Connected but no data — stale after reconnect
            fox.note_stale_after_reconnect()

        lag = self.event_loop.snapshot()
        if (lag.get("over_1000ms") or 0) > 0 or (lag.get("max_lag_ms") or 0) >= 500:
            issues.append(
                {
                    "error_code": "WS_EVENT_LOOP_LAG",
                    "severity": "WARNING",
                    "message": "Event loop lag elevated",
                    "details": {
                        **lag,
                        "probable_root_cause": RootCauseConfidence.CORRELATION_ONLY.value,
                        "confidence": RootCauseConfidence.CORRELATION_ONLY.value,
                        "exact_location": "backend/app/ingestion/ws_forensics.py:EventLoopLagMonitor",
                        "note": "Correlation with disconnects requires aligned timestamps; not causal proof",
                    },
                    "expected": "lag < 100ms typical",
                    "actual": f"max={lag.get('max_lag_ms')}ms p99={lag.get('p99_lag_ms')}",
                }
            )

        for orphan in self._orphan_alerts[-10:]:
            issues.append(
                {
                    "error_code": "WS_ORPHAN_CONNECTION",
                    "severity": "WARNING",
                    "message": orphan.get("message") or "Orphan WS connection/task",
                    "details": {
                        **orphan,
                        "probable_root_cause": RootCauseConfidence.STRONG_EVIDENCE.value,
                        "confidence": RootCauseConfidence.STRONG_EVIDENCE.value,
                    },
                }
            )

        # Deduplicate by error_code+connection_id within this snapshot
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for iss in issues:
            key = f"{iss.get('error_code')}:{iss.get('details', {}).get('connection_id')}:{iss.get('message')}"
            if key in seen:
                continue
            seen.add(key)
            iss.setdefault("first_seen", _iso(now))
            iss.setdefault("last_seen", _iso(now))
            iss.setdefault("count", 1)
            iss.setdefault(
                "affected_connections",
                [iss.get("details", {}).get("connection_id")]
                if iss.get("details", {}).get("connection_id")
                else [],
            )
            unique.append(iss)
        return unique

    def disconnect_summary(self) -> dict[str, Any]:
        by_reason: dict[str, int] = {}
        by_code: dict[str, int] = {}
        by_load: dict[str, int] = {}
        total = 0
        for fox in self.all_connections():
            for ev in fox.disconnect_events:
                total += 1
                by_reason[ev.reason.value] = by_reason.get(ev.reason.value, 0) + 1
                code_k = str(ev.close_code) if ev.close_code is not None else "none"
                by_code[code_k] = by_code.get(code_k, 0) + 1
                by_load[ev.load_bucket] = by_load.get(ev.load_bucket, 0) + 1
        return {
            "total_disconnect_events": total,
            "by_reason": by_reason,
            "by_close_code": by_code,
            "by_stream_load_bucket": by_load,
        }

    def reconnect_summary(self) -> dict[str, Any]:
        return {
            "connections": [
                {
                    "connection_id": f.connection_id,
                    "name": f.name,
                    "reconnect_count": f.reconnect_count,
                    "reconnects_last_1m": f.reconnects_in(60),
                    "reconnects_last_5m": f.reconnects_in(300),
                    "reconnects_last_1h": f.reconnects_in(3600),
                    "duplicate_reconnect_blocked": f.duplicate_reconnect_blocked,
                    "reconnect_owner_active": f.reconnect_owner_active,
                }
                for f in self.all_connections()
            ]
        }

    def data_gap_summary(self) -> dict[str, Any]:
        by_class: dict[str, int] = {}
        total = 0
        for fox in self.all_connections():
            for g in fox.data_gaps:
                total += 1
                by_class[g.classification.value] = (
                    by_class.get(g.classification.value, 0) + 1
                )
        return {"total_gaps": total, "by_classification": by_class}

    def build_report(self) -> dict[str, Any]:
        connections = [f.to_summary() for f in self.all_connections()]
        issues = self.detect_issues()
        return {
            "connections": connections,
            "disconnect_summary": self.disconnect_summary(),
            "reconnect_summary": self.reconnect_summary(),
            "event_loop": self.event_loop.snapshot(),
            "subscription_health": {
                c["connection_id"]: c["subscription_health"] for c in connections
            },
            "data_gap_summary": self.data_gap_summary(),
            "issues_candidates": issues,
            "connection_count": len(connections),
        }


# Process-wide singleton
ws_forensics = WebSocketForensicsRegistry()
