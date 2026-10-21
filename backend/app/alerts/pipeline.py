"""Alert rules applied to signals: dedup, STATUS, LATE, plan, cost filter, rate limit, delivery, follow-ups,
daily summary. Every alert, skip and follow-up is one row in ``alerts.jsonl``.

Order of the not-sent codes (the first that applies is the row's ``code``; every one that applies is in
``flags``): SUPPRESSED_KILLED, LATE, plan errors (NO_TICK_SIZE, NO_ENGINE_STOP, NO_MARKET_DATA,
INVALID_STOP, NO_TESTED_TARGET, INVALID_TARGET), SKIPPED_COST, RATE_LIMITED. Duplicates are DUPLICATE.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from app.alerts.config import AlertConfig
from app.alerts.data import MarketData
from app.alerts.plan import Signal, base_asset, build_plan, render_alert, render_followup, render_summary, utc
from app.alerts.sender import PreviewWriter, ReplyingTelegramSender
from app.alerts.sources import SkipRecord
from app.alerts.tracker import DONE, expire_if_due, new_tracker, step

STATE_VERSION = 1


def fresh_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "seen": {},
        "sent_times": [],
        "trackers": {},
        "message_ids": {},
        "stats": {},
        "last_summary_day": None,
        "offsets": {"journal_offset": 0, "journal_lines": 0, "fwd1_seq": -1, "fwd3_line": 0},
        "fwd1_seen": [],
        "fwd3_seen": [],
        "deferred": [],
    }


def load_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
        if state.get("version") == STATE_VERSION:
            return state
    except (OSError, ValueError):
        pass
    return fresh_state()


def save_state(state: Mapping[str, Any], path: Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, p)


class AlertPipeline:
    def __init__(
        self,
        cfg: AlertConfig,
        *,
        out_dir: Path,
        data: MarketData,
        mode: str = "watch",
        sender: ReplyingTelegramSender | None = None,
        state: dict[str, Any] | None = None,
    ) -> None:
        if mode not in ("watch", "backfill"):
            raise ValueError(mode)
        self.cfg = cfg
        self.out_dir = Path(out_dir)
        self.log_path = self.out_dir / "alerts.jsonl"
        self.preview = PreviewWriter(self.out_dir / "preview.md")
        self.data = data
        self.mode = mode
        self.sender = sender
        self.state = state if state is not None else fresh_state()
        # Backfill never sends. Watch sends only with enabled=true, dry_run=false and a sender.
        self.delivering = mode == "watch" and cfg.sends_telegram and sender is not None and sender.enabled
        self.run_mode = "live" if self.delivering else ("backfill" if mode == "backfill" else "dry_run")

    # --- logging -----------------------------------------------------------------------------------
    def _log(self, now: datetime, row: dict[str, Any]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        full = {"ts": utc(now).isoformat(), "mode": self.run_mode, **row}
        with self.log_path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(full, default=str, sort_keys=True, ensure_ascii=False) + "\n")

    def _stats(self, when: Any, group: str) -> dict[str, float]:
        day = utc(when).date().isoformat()
        g = self.state["stats"].setdefault(day, {}).setdefault(
            group, {"sent": 0, "not_sent": 0, "filled": 0, "wins": 0, "losses": 0, "total_r": 0.0}
        )
        return g

    @staticmethod
    def _sig_fields(sig: Signal) -> dict[str, Any]:
        return {
            "alert_id": sig.alert_id,
            "dedup_key": sig.dedup_key,
            "source": sig.source,
            "strategy_id": sig.strategy_id,
            "symbol": sig.symbol,
            "side": sig.side,
            "timeframe": sig.timeframe,
            "signal_bar": sig.signal_bar.isoformat(),
            "bar_close": sig.bar_close.isoformat(),
            "decided_at": sig.decided_at.isoformat(),
            "journal_ref": sig.journal_ref,
            "position_id": sig.position_id,
            # P14: what live paper did with this signal, and why it did not open.
            "paper_outcome": sig.extra.get("live_outcome"),
            "paper_skip_reason": sig.extra.get("skip_reason"),
            "extra": sig.extra or None,
        }

    # --- signals -----------------------------------------------------------------------------------
    def handle(self, item: Signal | SkipRecord, now: datetime) -> dict[str, Any]:
        now = utc(now)
        if isinstance(item, SkipRecord):
            row = {"kind": "SKIP", "code": item.code, "reason": item.reason,
                   "status": self.cfg.status_for(item.signal.source), **self._sig_fields(item.signal)}
            self._log(now, row)
            return row
        sig = item
        key = sig.dedup_key
        if key in self.state["seen"]:
            row = {"kind": "SKIP", "code": "DUPLICATE", **self._sig_fields(sig)}
            self._log(now, row)
            return row
        self.state["seen"][key] = sig.alert_id

        cfg = self.cfg
        status = cfg.status_for(sig.source, sig.strategy_id)
        lag_min = (now - sig.bar_close).total_seconds() / 60.0
        killed = status == "KILLED" and not cfg.allow_killed
        late = lag_min > cfg.late_minutes_for(sig.source)
        plan, err = build_plan(sig, cfg)
        cost = bool(plan and plan["cost_skip"])
        flags = {"killed": killed, "late": late, "plan_error": err, "cost": cost if plan else None}
        if killed:
            code = "SUPPRESSED_KILLED"
        elif late:
            code = "LATE"
        elif err:
            code = err
        elif cost:
            code = "SKIPPED_COST"
        else:
            code = None
        if code is None:
            window = timedelta(minutes=float(cfg.raw["rate_limit"]["window_minutes"]))
            recent = [t for t in self.state["sent_times"] if now - utc(t) < window]
            self.state["sent_times"] = recent
            if len(recent) >= int(cfg.raw["rate_limit"]["max_alerts"]):
                code = "RATE_LIMITED"
                flags["rate_limited"] = True

        text = render_alert(sig, plan, status, cfg, sent_at=now) if plan else None
        header = f"{sig.decided_at.isoformat()} · {sig.alert_id} · {sig.strategy_id} {sig.symbol} {sig.side} {sig.timeframe} bar {sig.signal_bar.isoformat()}"
        row: dict[str, Any] = {"status": status, "lag_minutes": round(lag_min, 2), "flags": flags,
                               "plan": _plan_log(plan), "text": text, **self._sig_fields(sig)}
        if code is None:
            assert text is not None and plan is not None
            if self.delivering:
                msg_id = self.sender.send(text) if self.sender else None
                code = "SENT" if msg_id is not None else "SEND_FAILED"
                if msg_id is not None:
                    self.state["message_ids"][sig.alert_id] = msg_id
                row["telegram_message_id"] = msg_id
            else:
                code = "DRY_RUN"
                self.preview.write(f"WOULD SEND · {header}", text)
            self.state["sent_times"].append(now.isoformat())
            self._stats(now, status)["sent"] += 1
            tr = new_tracker(sig, plan, cfg, sent=True)
            tr.update(status=status, journal_ref=sig.journal_ref, strategy_id=sig.strategy_id)
            self.state["trackers"][sig.alert_id] = tr
            row.update(kind="ALERT", code=code)
        else:
            self._stats(now, status)["not_sent"] += 1
            row.update(kind="SKIP", code=code)
            if not self.delivering and plan is not None:
                pv = cfg.raw.get("preview") or {}
                if pv.get("render_suppressed", True):
                    self.preview.write(f"NOT SENT ({code}) · {header}", text or "")
                if pv.get("track_suppressed_in_dry_run", True):
                    tr = new_tracker(sig, plan, cfg, sent=False)
                    tr.update(status=status, journal_ref=sig.journal_ref, strategy_id=sig.strategy_id, not_sent_code=code)
                    self.state["trackers"][sig.alert_id] = tr
        self._log(now, row)
        return row

    # --- follow-ups --------------------------------------------------------------------------------
    def update_trackers(self, now: datetime) -> int:
        now = utc(now)
        n = 0
        for aid in list(self.state["trackers"]):
            tr = self.state["trackers"][aid]
            try:
                bars = self.data.closed_bars(tr["symbol"], tr["timeframe"], utc(tr["next_bar"]), now)
            except Exception:  # noqa: BLE001 - retried next poll
                continue
            for bar in bars:
                for ev in step(tr, bar):
                    self._followup(tr, ev, now)
                    n += 1
                if tr["state"] == DONE:
                    break
            for ev in expire_if_due(tr, now):
                self._followup(tr, ev, now)
                n += 1
            if tr["state"] == DONE:
                del self.state["trackers"][aid]
        return n

    def _followup(self, tr: dict[str, Any], ev: dict[str, Any], now: datetime) -> None:
        text = render_followup(ev, tr)
        sent = bool(tr.get("sent"))
        when = ev.get("at") or now.isoformat()
        if sent:
            g = self._stats(when, str(tr.get("status")))
            if ev["kind"] == "fill":
                g["filled"] += 1
            elif ev["kind"] == "final":
                r = float(ev["r"])
                g["total_r"] += r
                if r > 0:
                    g["wins"] += 1
                elif r < 0:
                    g["losses"] += 1
        row: dict[str, Any] = {
            "kind": "FOLLOWUP", "event": ev["kind"], "alert_id": tr["alert_id"], "reply_to": tr["alert_id"],
            "alert_sent": sent, "not_sent_code": tr.get("not_sent_code"), "status": tr.get("status"),
            "strategy_id": tr.get("strategy_id"), "symbol": tr["symbol"], "side": tr["side"],
            "timeframe": tr["timeframe"], "event_at": when, "price": ev.get("price"), "r": ev.get("r"),
            "target": ev.get("target"), "exit": ev.get("exit"), "journal_ref": tr.get("journal_ref"), "text": text,
        }
        if sent and self.delivering:
            reply_to = self.state["message_ids"].get(tr["alert_id"])
            msg_id = self.sender.send(text, reply_to=reply_to) if self.sender else None
            row.update(code="SENT" if msg_id is not None else "SEND_FAILED", telegram_message_id=msg_id,
                       telegram_reply_to=reply_to)
        else:
            row["code"] = "DRY_RUN" if sent else "NOT_SENT_TRACKED"
            tag = "follow-up" if sent else f"follow-up, alert NOT SENT ({tr.get('not_sent_code')})"
            self.preview.write(f"{when} · {tag} · reply to {tr['alert_id']} · {base_asset(tr['symbol'])}", text)
        self._log(now, row)

    # --- daily summary -----------------------------------------------------------------------------
    def maybe_daily_summary(self, now: datetime) -> list[str]:
        now = utc(now)
        hour = int(self.cfg.raw["daily_summary_hour_utc"])
        due_day = (now - timedelta(hours=hour)).date() - timedelta(days=1)
        last = self.state.get("last_summary_day")
        if last is None:
            # Nothing is summarized for days before the sidecar first ran.
            self.state["last_summary_day"] = due_day.isoformat()
            return []
        out = []
        day = datetime.fromisoformat(last).date() + timedelta(days=1)
        while day <= due_day:
            out.append(self.emit_summary(day.isoformat(), self.state["stats"].get(day.isoformat(), {}), now))
            self.state["last_summary_day"] = day.isoformat()
            day += timedelta(days=1)
        return out

    def emit_summary(self, label: str, groups: Mapping[str, Any], now: datetime, *, title: str = "DAILY SUMMARY") -> str:
        text = render_summary(label, groups, title=title)
        row: dict[str, Any] = {"kind": "SUMMARY", "day": label, "groups": groups, "text": text}
        if self.delivering:
            msg_id = self.sender.send(text) if self.sender else None
            row.update(code="SENT" if msg_id is not None else "SEND_FAILED", telegram_message_id=msg_id)
        else:
            row["code"] = "DRY_RUN"
            self.preview.write(f"{utc(now).isoformat()} · {title} {label}", text)
        self._log(now, row)
        return text


def _plan_log(plan: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not plan:
        return None
    keys = ("tick", "signal_close", "atr", "zone_capped_by_sl", "zone_lo", "zone_hi", "entry_mid", "stop", "r_unit",
            "targets", "t2_tested", "rr_tested", "rr_raw", "t2_below_min_r", "stop_pct", "leverage",
            "leverage_needed", "leverage_cap_bound", "risk_pct_shown", "round_trip_cost", "stop_frac",
            "cost_floor_frac", "cost_skip", "rounded")
    return {k: plan[k] for k in keys}


def merge_stats(stats: Mapping[str, Mapping[str, Mapping[str, float]]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for groups in stats.values():
        for name, g in groups.items():
            acc = out.setdefault(name, {"sent": 0, "not_sent": 0, "filled": 0, "wins": 0, "losses": 0, "total_r": 0.0})
            for k, v in g.items():
                acc[k] = acc.get(k, 0) + v
    return out
