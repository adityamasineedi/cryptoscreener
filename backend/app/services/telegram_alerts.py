"""Telegram delivery for v1 paper alerts — subscriber on AlertFeed.

Does not generate signals. Filters PAPER_ENTRY / PAPER_EXIT to COMBO_02 v1
payloads and posts via Bot API without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import httpx
import structlog

from app.research.v1_production import V1_SYMBOLS
from app.services.alerts import AlertFeed, get_alert_feed

logger = structlog.get_logger(__name__)

_TELEGRAM_API = "https://api.telegram.org"
_SEND_TIMEOUT = 15.0
_V1_ALERT_TYPES = frozenset({"PAPER_ENTRY", "PAPER_EXIT"})
# Keep in sync with v1 paper watcher books (frozen trio + extended majors).
_V1_SYMBOLS = frozenset(V1_SYMBOLS)
_V1_COMBO_VERSION = "v1-combo02-long-htf"
_V1_PATH = "A"
_V1_TIMEFRAME = "1h"
_BLOCKED_STRATEGIES = frozenset(
    {
        "COMBO_02_V2_RESEARCH",
        "COMBO_02_SHORT_RESEARCH",
        "SHORT_PULLBACK_REJECTION_RESEARCH",
        "COMBO_02_SHORT_ENTRY_RESEARCH",
        "EXPERIMENTAL_PATH_B",
    }
)
_BLOCKED_SOURCES = frozenset(
    {
        "DYNAMIC_CANDIDATE_PIPELINE",
        "V2_CANDIDATE_PAPER_WATCHER",
        "SHORT_RESEARCH_PIPELINE",
        "SHORT_ENTRY_DIAGNOSTIC_PIPELINE",
    }
)
_BLOCKED_COMBO_VERSIONS = frozenset(
    {
        "v2-short-research",
        "v1-short-pullback-rejection",
        "v2-short-entry-research",
        "experimental-path-b",
    }
)


def _f(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_price(value: Any) -> str:
    n = _f(value)
    if n is None:
        return "—"
    if abs(n) >= 1000:
        return f"{n:,.2f}"
    if abs(n) >= 1:
        return f"{n:.4f}".rstrip("0").rstrip(".")
    return f"{n:.6f}".rstrip("0").rstrip(".")


def _fmt_pct(value: Any) -> str:
    n = _f(value)
    if n is None:
        return "—"
    # Stored as fraction (0.015) or already percent (>1)
    pct = n * 100.0 if abs(n) <= 1.0 else n
    text = f"{pct:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def _fmt_money(value: Any) -> str:
    n = _f(value)
    if n is None:
        return "—"
    return f"{n:.2f}"


def _fmt_r(value: Any) -> str:
    n = _f(value)
    if n is None:
        return "—"
    return f"{n:.2f}R"


def _fmt_qty(value: Any) -> str:
    n = _f(value)
    if n is None or n <= 0:
        return "—"
    if n >= 1000:
        return f"{n:,.3f}".rstrip("0").rstrip(".")
    if n >= 1:
        return f"{n:.4f}".rstrip("0").rstrip(".")
    return f"{n:.6f}".rstrip("0").rstrip(".")


def _base_asset(symbol: str) -> str:
    sym = str(symbol or "").upper()
    for quote in ("USDT", "USDC", "BUSD", "USD"):
        if sym.endswith(quote) and len(sym) > len(quote):
            return sym[: -len(quote)]
    return sym or "?"


def _stop_distance(entry: float | None, stop: float | None) -> float | None:
    if entry is None or stop is None:
        return None
    dist = abs(entry - stop)
    return dist if dist > 0 else None


def _fmt_dollar_price(value: Any) -> str:
    n = _f(value)
    if n is None:
        return "$—"
    return f"${_fmt_price(n)}"


def _resolve_leverage(payload: dict[str, Any], snip: dict[str, Any]) -> float:
    exe = snip.get("paper_execution") if isinstance(snip.get("paper_execution"), dict) else {}
    for src in (payload, snip, exe):
        lev = _f(src.get("leverage") if isinstance(src, dict) else None)
        if lev is not None and lev > 0:
            return lev
    return 2.0


def _entry_zone(entry: float | None, stop: float | None) -> tuple[float | None, float | None]:
    """Display-only entry band (does not change fill price)."""
    if entry is None:
        return None, None
    risk = _stop_distance(entry, stop)
    if risk is None:
        return entry, entry
    # Tight long buy zone just under the fill (≈15% of stop distance).
    lo = entry - (risk * 0.15)
    if stop is not None and lo <= stop:
        lo = entry
    return entry, lo


def _target_ladder(
    entry: float | None,
    stop: float | None,
    tp1: float | None,
) -> list[float]:
    """Display ladder from risk distance; prefers real TP1 as first step."""
    if entry is None:
        return []
    risk = _stop_distance(entry, stop)
    if risk is None:
        return [tp1] if tp1 is not None else []
    multiples = (1.0, 1.5, 2.0, 2.5, 3.0)
    ladder: list[float] = []
    if tp1 is not None and tp1 > entry:
        ladder.append(tp1)
    for m in multiples:
        level = entry + (risk * m)
        if tp1 is not None and abs(level - tp1) / max(abs(tp1), 1e-12) < 0.02:
            continue
        if ladder and level <= ladder[-1]:
            continue
        ladder.append(level)
        if len(ladder) >= 5:
            break
    while len(ladder) < 5 and risk > 0:
        nxt = entry + risk * (1.0 + 0.5 * len(ladder))
        if not ladder or nxt > ladder[-1]:
            ladder.append(nxt)
        else:
            break
    return ladder[:5]


def _fmt_target_line(targets: list[float]) -> str:
    if not targets:
        return "$—"
    if len(targets) == 1:
        return f"{_fmt_dollar_price(targets[0])}+"
    head = ", ".join(_fmt_dollar_price(t) for t in targets[:-1])
    return f"{head} & {_fmt_dollar_price(targets[-1])}+"


def _resolve_quantity(payload: dict[str, Any]) -> float | None:
    qty = _f(payload.get("quantity"))
    if qty is not None and qty > 0:
        return qty
    risk_usd = _f(payload.get("risk_usd"))
    dist = _stop_distance(_f(payload.get("entry_price")), _f(payload.get("stop_price")))
    if risk_usd is not None and risk_usd > 0 and dist is not None:
        return risk_usd / dist
    return None


def _snippet(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("signal_snippet") or {}
    return raw if isinstance(raw, dict) else {}


def _alert_pick(alert: dict[str, Any], payload: dict[str, Any], snip: dict[str, Any], *keys: str) -> Any:
    for src in (alert, payload, snip):
        for k in keys:
            if k in src and src.get(k) is not None and src.get(k) != "":
                return src.get(k)
    return None


def _is_research_blocked(
    *,
    strategy_id: str,
    source: str,
    combo_version: str,
    direction: str,
    path: str,
) -> bool:
    if strategy_id in _BLOCKED_STRATEGIES or source in _BLOCKED_SOURCES:
        return True
    if combo_version in _BLOCKED_COMBO_VERSIONS:
        return True
    if direction == "SHORT":
        return True
    if path in ("B", "PATH_B"):
        return True
    return False


def is_v1_paper_alert(alert: dict[str, Any] | None) -> bool:
    """Fail-closed COMBO_02 v1 Telegram filter — explicit fields only.

    Requires PAPER_ENTRY/EXIT + telegram_eligible + full v1 watcher identity
    + HTF_ALIGNED + universe symbol/timeframe. Never infers from titles.
    """
    if not alert or not isinstance(alert, dict):
        return False
    alert_type = str(alert.get("type") or "").upper()
    if alert_type not in _V1_ALERT_TYPES:
        return False

    payload = alert.get("payload") or {}
    if not isinstance(payload, dict):
        return False
    snip = _snippet(payload)

    # telegram_eligible must be explicitly True (fail closed if absent)
    tel = _alert_pick(alert, payload, snip, "telegram_eligible")
    if tel is not True and str(tel).lower() not in ("true", "1"):
        return False

    strategy_id = str(_alert_pick(alert, payload, snip, "strategy_id") or "")
    source = str(_alert_pick(alert, payload, snip, "source") or "")
    combo_id = str(_alert_pick(alert, payload, snip, "combo_id") or "").upper()
    combo_version = str(_alert_pick(alert, payload, snip, "combo_version") or "").lower()
    path = str(_alert_pick(alert, payload, snip, "path") or "").upper().replace("PATH_", "")
    symbol = str(_alert_pick(alert, payload, snip, "symbol") or alert.get("symbol") or "").upper()
    timeframe = str(_alert_pick(alert, payload, snip, "timeframe") or "").lower()
    htf = str(
        _alert_pick(alert, payload, snip, "htf_alignment") or snip.get("htf_alignment") or ""
    ).upper()
    direction = str(_alert_pick(alert, payload, snip, "direction", "side") or "").upper()

    if _is_research_blocked(
        strategy_id=strategy_id,
        source=source,
        combo_version=combo_version,
        direction=direction,
        path=path,
    ):
        return False

    if strategy_id != "COMBO_02_V1":
        return False
    if source != "V1_PAPER_WATCHER":
        return False
    if combo_id != "COMBO_02":
        return False
    if combo_version != _V1_COMBO_VERSION:
        return False
    if path != _V1_PATH:
        return False
    if timeframe != _V1_TIMEFRAME:
        return False
    if symbol not in _V1_SYMBOLS:
        return False
    if htf != "HTF_ALIGNED":
        return False
    return True


def is_telegram_trade_alert(alert: dict[str, Any] | None) -> bool:
    """Send gate for paper trade signals on all paper-eligible coins.

    Allows any PAPER_ENTRY / PAPER_EXIT with entry+stop that is not a blocked
    research/experimental stream. Includes COMBO_02 v1 books and legacy Path A
    paper opens across the risk-policy universe.
    """
    if is_v1_paper_alert(alert):
        return True
    if not alert or not isinstance(alert, dict):
        return False
    alert_type = str(alert.get("type") or "").upper()
    if alert_type not in _V1_ALERT_TYPES:
        return False

    payload = alert.get("payload") or {}
    if not isinstance(payload, dict):
        return False
    snip = _snippet(payload)

    strategy_id = str(_alert_pick(alert, payload, snip, "strategy_id") or "")
    source = str(_alert_pick(alert, payload, snip, "source") or "")
    combo_version = str(_alert_pick(alert, payload, snip, "combo_version") or "").lower()
    path = str(_alert_pick(alert, payload, snip, "path") or "").upper().replace("PATH_", "")
    direction = str(_alert_pick(alert, payload, snip, "direction", "side") or "LONG").upper()
    symbol = str(_alert_pick(alert, payload, snip, "symbol") or alert.get("symbol") or "").upper()

    if not symbol:
        return False
    if _is_research_blocked(
        strategy_id=strategy_id,
        source=source,
        combo_version=combo_version,
        direction=direction,
        path=path,
    ):
        return False

    # Only live paper streams (v1 watcher + legacy Path A / research-15m opens).
    if source and source not in (
        "V1_PAPER_WATCHER",
        "LEGACY_SETUP_SIGNAL",
        "",
    ):
        # Unknown sources: allow only when strategy is a known paper stream.
        if strategy_id not in ("COMBO_02_V1", "RESEARCH_15M", ""):
            return False

    entry = _f(payload.get("entry_price"))
    stop = _f(payload.get("stop_price"))
    if entry is None or stop is None or entry <= 0 or stop <= 0:
        return False
    return True


def _outcome_label(exit_reason: Any) -> str:
    reason = str(exit_reason or "EXIT").upper()
    if reason in ("STOP", "SL", "STOPPED"):
        return "SL"
    if reason.startswith("TP"):
        return reason
    return reason


def _target_r(payload: dict[str, Any], snip: dict[str, Any]) -> float | None:
    explicit = _f(snip.get("target_r")) or _f(payload.get("target_r"))
    if explicit is not None:
        return explicit
    entry = _f(payload.get("entry_price"))
    stop = _f(payload.get("stop_price"))
    tp1 = _f(payload.get("tp1_price"))
    if entry is None or stop is None or tp1 is None:
        return None
    risk = entry - stop
    if risk <= 0:
        return None
    return (tp1 - entry) / risk


def _principal(payload: dict[str, Any], snip: dict[str, Any]) -> float | None:
    explicit = _f(snip.get("principal")) or _f(payload.get("principal"))
    if explicit is not None and explicit > 0:
        return explicit
    risk_usd = _f(payload.get("risk_usd"))
    risk_pct = _f(snip.get("risk_percent"))
    if risk_usd is not None and risk_pct is not None and risk_pct > 0:
        # risk_percent stored as fraction
        frac = risk_pct if abs(risk_pct) <= 1.0 else risk_pct / 100.0
        if frac > 0:
            return risk_usd / frac
    return None


def format_telegram_message(alert: dict[str, Any]) -> str | None:
    """Build human-readable Telegram text for a v1 paper alert."""
    alert_type = str(alert.get("type") or "").upper()
    payload = alert.get("payload") if isinstance(alert.get("payload"), dict) else {}
    assert isinstance(payload, dict)
    snip = _snippet(payload)
    symbol = str(alert.get("symbol") or payload.get("symbol") or "?").upper()
    asset = _base_asset(symbol)
    side = str(payload.get("side") or snip.get("direction") or "LONG").upper()
    if side not in ("LONG", "SHORT"):
        side = "LONG"
    entry = _f(payload.get("entry_price"))
    stop = _f(payload.get("stop_price"))
    tp1 = _f(payload.get("tp1_price"))
    qty = _resolve_quantity(payload)
    notional = (qty * entry) if qty is not None and entry is not None else None
    leverage = _resolve_leverage(payload, snip)

    if alert_type == "PAPER_ENTRY":
        entry_hi, entry_lo = _entry_zone(entry, stop)
        if entry_hi is not None and entry_lo is not None and abs(entry_hi - entry_lo) > 0:
            entry_line = f"{_fmt_dollar_price(entry_hi)} - {_fmt_dollar_price(entry_lo)}"
        else:
            entry_line = _fmt_dollar_price(entry)
        targets = _target_ladder(entry, stop, tp1)
        return (
            f"SCALP TRADE - {asset}\n"
            f"🏮 TYPE - {side}\n"
            f"\n"
            f"👉 ENTRY - {entry_line}\n"
            f"👉 TARGET - {_fmt_target_line(targets)}\n"
            f"👉 SL - {_fmt_dollar_price(stop)}\n"
            f"\n"
            f"🚨LEVERAGE - {leverage:g}x"
        )

    if alert_type == "PAPER_EXIT":
        outcome = _outcome_label(payload.get("exit_reason"))
        hold = payload.get("hold_bars")
        if hold is None:
            hold = snip.get("hold_bars")
        hold_s = str(hold) if hold is not None else "n/a"
        qty_line = f"Qty: {_fmt_qty(qty)} {asset}"
        if notional is not None:
            qty_line += f" (entry ~${_fmt_money(notional)})"
        return (
            f"CLOSE TRADE - {asset}\n"
            f"🏮 TYPE - {side}\n"
            f"\n"
            f"👉 EXIT - {_fmt_dollar_price(payload.get('exit_price'))} ({outcome})\n"
            f"👉 ENTRY - {_fmt_dollar_price(entry)}\n"
            f"👉 SL - {_fmt_dollar_price(stop)}\n"
            f"\n"
            f"R: {_fmt_r(payload.get('r_multiple'))} | "
            f"PnL: ${_fmt_money(payload.get('pnl_usd'))}\n"
            f"{qty_line}\n"
            f"Hold: {hold_s} bars"
        )

    return None


class TelegramAlertSubscriber:
    """AlertFeed subscriber that posts v1 paper alerts to Telegram."""

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        feed: AlertFeed | None = None,
        api_base: str = _TELEGRAM_API,
        timeout: float = _SEND_TIMEOUT,
    ) -> None:
        self.bot_token = str(bot_token).strip()
        self.chat_id = str(chat_id).strip()
        self.feed = feed or get_alert_feed()
        self.api_base = api_base.rstrip("/")
        self.timeout = float(timeout)
        self._attached = False
        self._client: httpx.AsyncClient | None = None
        self._notified_entry: set[str] = set()
        self._notified_exit: set[str] = set()

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def start(self) -> bool:
        if not self.enabled:
            logger.warning(
                "telegram_alerts_disabled",
                reason="missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID",
            )
            return False
        if self._attached:
            return True
        self.feed.subscribe(self)
        self._attached = True
        logger.info("telegram_alerts_subscribed", chat_id=self.chat_id)
        return True

    def stop(self) -> None:
        if self._attached:
            self.feed.unsubscribe(self)
            self._attached = False
        client = self._client
        self._client = None
        if client is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(client.aclose())
            except RuntimeError:
                try:
                    asyncio.run(client.aclose())
                except Exception:  # noqa: BLE001
                    pass
        logger.info("telegram_alerts_unsubscribed")

    def __call__(self, alert: dict[str, Any]) -> None:
        """Sync fan-out entry — schedules send without blocking callers."""
        try:
            if not is_telegram_trade_alert(alert):
                return
            payload = alert.get("payload") if isinstance(alert.get("payload"), dict) else {}
            assert isinstance(payload, dict)
            trade_id = str(payload.get("id") or alert.get("id") or "")
            alert_type = str(alert.get("type") or "").upper()
            if trade_id:
                if alert_type == "PAPER_ENTRY" and trade_id in self._notified_entry:
                    return
                if alert_type == "PAPER_EXIT" and trade_id in self._notified_exit:
                    return
            text = format_telegram_message(alert)
            if not text:
                return
            self._dispatch(text, alert, trade_id=trade_id, alert_type=alert_type)
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram_alert_handler_failed", error=str(exc))

    def _dispatch(
        self,
        text: str,
        alert: dict[str, Any],
        *,
        trade_id: str = "",
        alert_type: str = "",
    ) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self._send_async(text, alert, trade_id=trade_id, alert_type=alert_type),
                name="telegram_alert_send",
            )
        except RuntimeError:
            threading.Thread(
                target=self._send_sync,
                args=(text, alert, trade_id, alert_type),
                daemon=True,
                name="telegram-alert",
            ).start()

    async def _send_async(
        self,
        text: str,
        alert: dict[str, Any],
        *,
        trade_id: str = "",
        alert_type: str = "",
    ) -> None:
        url = f"{self.api_base}/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=self.timeout)
            resp = await self._client.post(url, json=payload)
            if resp.status_code >= 400:
                logger.warning(
                    "telegram_send_failed",
                    status=resp.status_code,
                    body=resp.text[:300],
                    symbol=alert.get("symbol"),
                    type=alert.get("type"),
                )
                return
            self._mark_notified(trade_id, alert_type)
            self._log_sent(alert)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "telegram_send_error",
                error=str(exc),
                symbol=alert.get("symbol"),
                type=alert.get("type"),
            )

    def _send_sync(
        self,
        text: str,
        alert: dict[str, Any],
        trade_id: str = "",
        alert_type: str = "",
    ) -> None:
        url = f"{self.api_base}/bot{self.bot_token}/sendMessage"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    url,
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "disable_web_page_preview": True,
                    },
                )
            if resp.status_code >= 400:
                logger.warning(
                    "telegram_send_failed",
                    status=resp.status_code,
                    body=resp.text[:300],
                    symbol=alert.get("symbol"),
                    type=alert.get("type"),
                )
                return
            self._mark_notified(trade_id, alert_type)
            self._log_sent(alert)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "telegram_send_error",
                error=str(exc),
                symbol=alert.get("symbol"),
                type=alert.get("type"),
            )

    def _mark_notified(self, trade_id: str, alert_type: str) -> None:
        if not trade_id:
            return
        if alert_type == "PAPER_ENTRY":
            self._notified_entry.add(trade_id)
        elif alert_type == "PAPER_EXIT":
            self._notified_exit.add(trade_id)

    def _log_sent(self, alert: dict[str, Any]) -> None:
        payload = alert.get("payload") if isinstance(alert.get("payload"), dict) else {}
        assert isinstance(payload, dict)
        logger.info(
            "telegram_alert_sent",
            symbol=alert.get("symbol"),
            type=alert.get("type"),
            outcome=_outcome_label(payload.get("exit_reason"))
            if str(alert.get("type") or "").upper() == "PAPER_EXIT"
            else "ENTRY",
            r_multiple=payload.get("r_multiple"),
            pnl_usd=payload.get("pnl_usd"),
        )


_active_subscriber: TelegramAlertSubscriber | None = None


def get_telegram_alert_subscriber() -> TelegramAlertSubscriber | None:
    return _active_subscriber


def telegram_delivery_status(settings: Any | None = None) -> dict[str, Any]:
    """Safe (no secrets) Telegram delivery status for Paper / ops UI."""
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    flag = bool(getattr(settings, "paper_v1_telegram_enabled", False))
    token = getattr(settings, "telegram_bot_token", None) or None
    chat_id = getattr(settings, "telegram_chat_id", None) or None
    token_ok = bool(str(token).strip() if token else "")
    chat_s = str(chat_id).strip() if chat_id else ""
    chat_ok = bool(chat_s)
    sub = _active_subscriber
    attached = bool(sub is not None and getattr(sub, "_attached", False))
    if not flag:
        reason = "PAPER_V1_TELEGRAM_ENABLED is false"
    elif not token_ok or not chat_ok:
        reason = "missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"
    elif not attached:
        reason = "subscriber not attached (restart backend after enabling)"
    else:
        reason = None
    return {
        "enabled": flag,
        "configured": token_ok and chat_ok,
        "subscribed": attached,
        "ready": flag and token_ok and chat_ok and attached,
        "chat_id_set": chat_ok,
        "monitors": sorted(_V1_SYMBOLS),
        "timeframe": "any_paper",
        "alert_types": sorted(_V1_ALERT_TYPES),
        "source": "V1_PAPER_WATCHER|LEGACY_SETUP_SIGNAL",
        "combo_version": _V1_COMBO_VERSION,
        "path": "A|LEGACY",
        "reason": reason,
        "label": (
            "Telegram · PAPER_ENTRY / PAPER_EXIT for all paper-eligible coins "
            "(v1 books + legacy Path A)"
        ),
        "v1_books": sorted(_V1_SYMBOLS),
        "legacy_paper": True,
    }


def start_telegram_alerts(
    settings: Any | None = None,
    *,
    feed: AlertFeed | None = None,
) -> TelegramAlertSubscriber | None:
    """Instantiate + subscribe when Telegram env is configured and enabled."""
    global _active_subscriber
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    if not bool(getattr(settings, "paper_v1_telegram_enabled", False)):
        logger.info(
            "telegram_alerts_disabled",
            reason="PAPER_V1_TELEGRAM_ENABLED is false (default until parity passes)",
        )
        _active_subscriber = None
        return None
    token = getattr(settings, "telegram_bot_token", None) or None
    chat_id = getattr(settings, "telegram_chat_id", None) or None
    token_s = str(token).strip() if token else ""
    chat_s = str(chat_id).strip() if chat_id else ""
    if not token_s or not chat_s:
        logger.warning(
            "telegram_alerts_disabled",
            reason="missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID",
        )
        _active_subscriber = None
        return None
    sub = TelegramAlertSubscriber(bot_token=token_s, chat_id=chat_s, feed=feed)
    sub.start()
    _active_subscriber = sub
    return sub


def stop_telegram_alerts() -> None:
    global _active_subscriber
    if _active_subscriber is not None:
        _active_subscriber.stop()
        _active_subscriber = None
