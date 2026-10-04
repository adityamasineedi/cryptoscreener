"""Telegram OUTPUT-ONLY validation for parity research (mock/test path).

Never enables dynamic production Telegram. production_approved stays false.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from app.research.live_backtest_parity.constants import (
    PRODUCTION_APPROVED,
    TELEGRAM_ELIGIBLE,
)
from app.research.live_backtest_parity.models import ParityEvent, iso_utc


REQUIRED_ALERT_FIELDS = (
    "strategy_id",
    "source",
    "symbol",
    "timeframe",
    "direction",
    "entry",
    "SL",
    "TP",
    "RR",
    "production_approved",
    "telegram_eligible",
    "entry_price_status",
)


def validate_alert_preflight(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate structured alert before any send attempt."""
    missing = [k for k in REQUIRED_ALERT_FIELDS if k not in payload]
    errors: list[str] = []
    if missing:
        errors.append(f"missing_fields:{','.join(missing)}")

    prod = payload.get("production_approved")
    tel = payload.get("telegram_eligible")

    # Dynamic / research candidates: production_approved MUST remain false.
    if prod is True or str(prod).lower() in ("true", "1"):
        errors.append("production_approved_must_remain_false")

    # telegram_eligible must remain false unless explicitly approved paper-alert path
    approved_paper_path = bool(payload.get("explicit_paper_alert_path"))
    if (tel is True or str(tel).lower() in ("true", "1")) and not approved_paper_path:
        errors.append("telegram_eligible_blocked_for_dynamic_research")

    ok = not errors
    return {
        "ok": ok,
        "errors": errors,
        "production_approved": False,
        "telegram_eligible": False if not approved_paper_path else bool(tel),
    }


def build_research_alert_payload(event: ParityEvent) -> dict[str, Any]:
    """Structured payload — shows backtest / live / paper entries distinctly."""
    base = event.alert_payload()
    # Map SL/TP/RR aliases already present; add canonical "entry" as triad note.
    base["entry"] = {
        "backtest_entry": event.entry_triad.backtest_entry,
        "live_signal_price": event.entry_triad.live_signal_price,
        "paper_entry": event.entry_triad.paper_entry,
    }
    base["production_approved"] = PRODUCTION_APPROVED
    base["telegram_eligible"] = TELEGRAM_ELIGIBLE
    base["explicit_paper_alert_path"] = False
    return base


def format_research_alert_text(payload: Mapping[str, Any]) -> str:
    entry = payload.get("entry") if isinstance(payload.get("entry"), Mapping) else {}
    return (
        f"[RESEARCH SIMULATION ONLY]\n"
        f"{payload.get('symbol')} {payload.get('timeframe')} "
        f"{payload.get('direction')}\n"
        f"backtest_entry={entry.get('backtest_entry')} "
        f"live_signal_price={entry.get('live_signal_price')} "
        f"paper_entry={entry.get('paper_entry')}\n"
        f"dev%={payload.get('entry_deviation_pct')} "
        f"SL={payload.get('SL')} TP={payload.get('TP')} RR={payload.get('RR')}\n"
        f"entry_price_status={payload.get('entry_price_status')} "
        f"parity={payload.get('status')}\n"
        f"production_approved=false telegram_eligible=false\n"
        f"NO LIVE OR PAPER PRODUCTION ALERT"
    )


class MockTelegramSink:
    """Safe test/mock alert mechanism — never hits Telegram Bot API."""

    def __init__(self) -> None:
        self.generated: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.blocked: list[dict[str, Any]] = []
        self.failed: list[dict[str, Any]] = []

    def process(self, event: ParityEvent) -> dict[str, Any]:
        payload = build_research_alert_payload(event)
        generated_at = datetime.now(timezone.utc)
        event.timeline.alert_generated_at = generated_at
        record = {
            "event_id": event.event_id,
            "alert_generated_at": iso_utc(generated_at),
            "payload": payload,
            "text": format_research_alert_text(payload),
        }
        self.generated.append(record)

        pre = validate_alert_preflight(payload)
        if not pre["ok"]:
            event.telegram_status = "blocked"
            blocked = {
                **record,
                "delivery_status": "blocked",
                "errors": pre["errors"],
                "alert_sent_at": None,
                "message_id": None,
            }
            self.blocked.append(blocked)
            return blocked

        # Research path intentionally does not send production Telegram.
        # Record as blocked (policy) rather than sent — safe mock.
        # Optional: mark "sent" only for explicit mock delivery flag.
        if payload.get("force_mock_delivery") is True:
            sent_at = datetime.now(timezone.utc)
            message_id = f"mock-{uuid.uuid4().hex[:12]}"
            event.timeline.telegram_sent_at = sent_at
            event.telegram_status = "sent"
            event.telegram_message_id = message_id
            sent = {
                **record,
                "delivery_status": "sent",
                "alert_sent_at": iso_utc(sent_at),
                "message_id": message_id,
            }
            self.sent.append(sent)
            return sent

        event.telegram_status = "blocked"
        blocked = {
            **record,
            "delivery_status": "blocked",
            "errors": ["research_mock_no_production_telegram"],
            "alert_sent_at": None,
            "message_id": None,
        }
        self.blocked.append(blocked)
        return blocked

    def stats(self) -> dict[str, int]:
        return {
            "generated": len(self.generated),
            "sent": len(self.sent),
            "blocked": len(self.blocked),
            "failed": len(self.failed),
        }
