"""Alert rules applied to signals: dedup, STATUS, LATE, plan, cost filter, rate limit, delivery, follow-ups,
daily summary. Every alert, skip and follow-up is one row in ``alerts.jsonl``.

Order of the not-sent codes (the first that applies is the row's ``code``; every one that applies is in
``flags``): SUPPRESSED_KILLED, LATE, NOT_OPENED, plan errors (NO_TICK_SIZE, NO_ENGINE_STOP, NO_MARKET_DATA,
INVALID_STOP, NO_TESTED_TARGET, INVALID_TARGET), SKIPPED_COST, SKIPPED_COST_STOP, RATE_LIMITED. Duplicates
are DUPLICATE. Before all of them, a source's excluded symbol (D108/D120: no directional BTC alert) is
EXCLUDED_SYMBOL.

Owner ruling D145: NOT_OPENED = a paper-book signal whose paper outcome is not "opened" (sent anyway, marked,
only with ``send_not_opened`` true); SKIPPED_COST_STOP = entry-to-stop distance under 3x the D144 round trip.

Owner ruling D149: the daily summary's main section counts only sent alerts for trades a paper book OPENED and
shows the paper ledger's result for each (paper_trades, read only); the tracker's numbers follow under
"alert levels touched (not paper trades)".

Owner rulings D150, D193(a), D194 (DECISIONS D221), v4:
- a KILLED source listed in ``killed_opened_exception_sources`` (empty by default; the v4 config lists
  PASSED_BOOK) is not SUPPRESSED_KILLED when its paper outcome is "opened": every other rule still applies, and
  its alert, follow-ups and summary line carry the config's label right after the D117 line. Its other signals
  stay SUPPRESSED_KILLED; ``allow_killed`` is unchanged for every source;
- ledger results: a sent alert for an exception trade (paper ledger, D149), a FWD-1 trade or a FWD-3 trade
  (their own ledgers, D150, read only) gets its result as a reply from that ledger. The alert-level tracker
  still runs for those alerts, but its replies are logged as LEVELS_ONLY and not posted (they are alert levels,
  not the trade); when it ends and the ledger has no result yet, the reply says the result is pending.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from app.alerts.config import AlertConfig, timeframe_minutes
from app.alerts.data import MarketData
from app.alerts.fwd_ledgers import FWD_LEDGER_NAMES, PAPER_LEDGER_NAME, ForwardLedgers, paper_result
from app.alerts.plan import (
    Signal,
    base_asset,
    build_plan,
    render_alert,
    render_followup,
    render_ledger_followup,
    render_summary,
    utc,
)
from app.alerts.sender import PreviewWriter, ReplyingTelegramSender
from app.alerts.sources import BOOK_SOURCE, SkipRecord
from app.alerts.tracker import DONE, expire_if_due, new_tracker, step

STATE_VERSION = 1
# Owner rulings D108/D120: a source's excluded symbol (config symbol_exclusions), checked right after dedup.
EXCLUDED_SYMBOL = "EXCLUDED_SYMBOL"
# Owner ruling D145.
NOT_OPENED = "NOT_OPENED"
SKIPPED_COST_STOP = "SKIPPED_COST_STOP"
# Owner ruling D149: paper_trades signal_snippet sources per alert source (the books whose trades live there).
LEDGER_SNIPPET_SOURCES: dict[str, list[str]] = {}
for _snip, _src in BOOK_SOURCE.items():
    LEDGER_SNIPPET_SOURCES.setdefault(_src, []).append(_snip)
# v4: a ledger-result alert's alert-level tracker replies are logged with this code, never posted.
LEVELS_ONLY = "LEVELS_ONLY"
# v4: how long a ledger-result alert waits for its ledger's result before it is dropped from the state.
LEDGER_WATCH_DEFAULT_DAYS = 45.0


def not_opened_reason(sig: Signal, cfg: AlertConfig) -> str | None:
    """Why the paper book did not open this signal, or None when it opened it or there is no paper book.

    A paper-book source (config ``paper_book_sources``) or any non-screener signal carrying a paper outcome
    alerts only when that outcome is "opened" (owner ruling D145). A missing outcome counts as not opened.
    """
    outcome = sig.extra.get("live_outcome")
    if not cfg.has_paper_outcome(sig.source, outcome) or outcome == "opened":
        return None
    return str(sig.extra.get("skip_reason") or outcome or "no_paper_outcome")


def fresh_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "seen": {},
        "sent_times": [],
        "trackers": {},
        "message_ids": {},
        "stats": {},
        "last_summary_day": None,
        "summary_alerts": {},
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
        fwd_ledgers: ForwardLedgers | None = None,
    ) -> None:
        if mode not in ("watch", "backfill"):
            raise ValueError(mode)
        self.cfg = cfg
        # D150: the FWD-1 / FWD-3 ledgers, read only. None (tests' default) reads no file: results stay pending.
        self.fwd_ledgers = fwd_ledgers or ForwardLedgers()
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
        if cfg.symbol_excluded(sig.source, sig.symbol):
            # Owner rulings D108/D120: no directional BTC alert. Logged only: never sent, rendered or tracked.
            self._stats(now, status)["not_sent"] += 1
            row = {"kind": "SKIP", "code": EXCLUDED_SYMBOL, "reason": cfg.symbol_exclusion_reason,
                   "status": status, **self._sig_fields(sig)}
            self._log(now, row)
            return row
        lag_min = (now - sig.bar_close).total_seconds() / 60.0
        # v4 (D150/D193a/D194): a listed KILLED source's OPENED paper trade is not suppressed; it is labelled.
        exception = cfg.killed_opened_exception(sig.source, status, sig.extra.get("live_outcome"))
        killed_label = cfg.killed_opened_exception_label if exception else None
        killed = status == "KILLED" and not cfg.allow_killed and not exception
        late = lag_min > cfg.late_minutes_for(sig.source)
        not_opened = not_opened_reason(sig, cfg)
        plan, err = build_plan(sig, cfg)
        cost = bool(plan and plan["cost_skip"])
        cost_stop = bool(plan and plan["cost_stop_skip"])
        flags = {"killed": killed, "late": late, "not_opened": not_opened, "plan_error": err,
                 "cost": cost if plan else None, "cost_stop": cost_stop if plan else None}
        if exception:
            flags["killed_opened_exception"] = True
        if killed:
            code = "SUPPRESSED_KILLED"
        elif late:
            code = "LATE"
        elif not_opened is not None and not cfg.send_not_opened:
            code = NOT_OPENED
        elif err:
            code = err
        elif cost:
            code = "SKIPPED_COST"
        elif cost_stop:
            code = SKIPPED_COST_STOP
        else:
            code = None
        if code is None:
            window = timedelta(minutes=float(cfg.raw["rate_limit"]["window_minutes"]))
            recent = [t for t in self.state["sent_times"] if now - utc(t) < window]
            self.state["sent_times"] = recent
            if len(recent) >= int(cfg.raw["rate_limit"]["max_alerts"]):
                code = "RATE_LIMITED"
                flags["rate_limited"] = True

        text = render_alert(sig, plan, status, cfg, sent_at=now, not_opened_reason=not_opened,
                            killed_label=killed_label) if plan else None
        header = f"{sig.decided_at.isoformat()} · {sig.alert_id} · {sig.strategy_id} {sig.symbol} {sig.side} {sig.timeframe} bar {sig.signal_bar.isoformat()}"
        row: dict[str, Any] = {"status": status, "lag_minutes": round(lag_min, 2), "flags": flags,
                               "plan": _plan_log(plan), "text": text, **self._sig_fields(sig)}
        if code == NOT_OPENED:
            row["reason"] = not_opened
        ledger = self._result_ledger(sig, exception)
        if ledger is not None and code is None:
            row["result_ledger"] = ledger
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
            if killed_label:
                tr["killed_label"] = killed_label
            if ledger is not None:
                tr["ledger_result"] = ledger
                self._watch_ledger(sig, status, plan, ledger, killed_label, now)
            self.state["trackers"][sig.alert_id] = tr
            self._record_sent(sig, status, now, not_opened, killed_label)
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
                    if killed_label:
                        tr["killed_label"] = killed_label
                    self.state["trackers"][sig.alert_id] = tr
        self._log(now, row)
        return row

    def _record_sent(self, sig: Signal, status: str, now: datetime, not_opened: str | None,
                     killed_label: str | None = None) -> None:
        """Owner ruling D149: every sent alert, for the daily summary (only ``opened`` paper-book ones count
        in its main section; a not-opened one sent under send_not_opened does not)."""
        paper_book = self.cfg.has_paper_outcome(sig.source, sig.extra.get("live_outcome"))
        day = now.date().isoformat()
        rec = {
            "alert_id": sig.alert_id, "source": sig.source, "strategy_id": sig.strategy_id, "status": status,
            "symbol": sig.symbol, "side": sig.side, "timeframe": sig.timeframe,
            "signal_bar": sig.signal_bar.isoformat(), "bar_close": sig.bar_close.isoformat(),
            "position_id": sig.position_id, "paper_book": paper_book,
            "opened": bool(paper_book and not_opened is None),
        }
        if killed_label:
            rec["killed_label"] = killed_label
        self.state.setdefault("summary_alerts", {}).setdefault(day, []).append(rec)

    # --- v4: results from the trade's own ledger (paper D149, FWD-1 / FWD-3 D150) ------------------------------
    @staticmethod
    def _result_ledger(sig: Signal, exception: bool) -> str | None:
        """Which ledger holds this alert's trade result: FWD-1 / FWD-3 for their trades, the paper ledger for
        an exception (KILLED, opened) trade; None keeps v3's alert-level follow-ups."""
        if sig.extra.get("live_outcome") != "opened":
            return None
        if sig.source in FWD_LEDGER_NAMES:
            return FWD_LEDGER_NAMES[sig.source]
        return PAPER_LEDGER_NAME if exception else None

    def _watch_ledger(self, sig: Signal, status: str, plan: Mapping[str, Any], ledger: str,
                      killed_label: str | None, now: datetime) -> None:
        days = float(self.cfg.raw.get("ledger_followup_max_days") or LEDGER_WATCH_DEFAULT_DAYS)
        w = {"alert_id": sig.alert_id, "ledger": ledger, "source": sig.source, "strategy_id": sig.strategy_id,
             "status": status, "symbol": sig.symbol, "side": sig.side, "timeframe": sig.timeframe,
             "signal_bar": sig.signal_bar.isoformat(), "bar_close": sig.bar_close.isoformat(),
             "position_id": sig.position_id, "tick": plan["tick"], "tracker_done": False, "pending_sent": False,
             "watch_until": (now + timedelta(days=days)).isoformat()}
        if killed_label:
            w["killed_label"] = killed_label
        self.state.setdefault("ledger_watch", {})[sig.alert_id] = w

    def ledger_result(self, a: Mapping[str, Any]) -> dict[str, Any] | None:
        """The trade's result in its own ledger: FWD-1 / FWD-3 from theirs (read only), else the paper ledger."""
        if str(a.get("source")) in FWD_LEDGER_NAMES:
            return self.fwd_ledgers.result(str(a.get("source")), a.get("position_id"))
        return paper_result(self.ledger_trade(a))

    def update_ledger_followups(self, now: datetime) -> int:
        """Reply with each watched alert's ledger result once the ledger records it; when its alert-level
        tracker has ended first, reply once that the result is pending. A failed read is retried next poll."""
        now = utc(now)
        watch = self.state.get("ledger_watch") or {}
        n = 0
        for aid in list(watch):
            w = watch[aid]
            try:
                res = self.ledger_result(w)
            except Exception:  # noqa: BLE001 - retried next poll
                continue
            if res is not None and str(res.get("status")) == "CLOSED":
                self._ledger_followup(w, res, now)
                del watch[aid]
                n += 1
            elif w.get("tracker_done") and not w.get("pending_sent"):
                self._ledger_followup(w, res, now)
                w["pending_sent"] = True
                n += 1
            elif now >= utc(w["watch_until"]):
                del watch[aid]
        return n

    def _ledger_followup(self, w: Mapping[str, Any], res: Mapping[str, Any] | None, now: datetime) -> None:
        text = render_ledger_followup(w, res)
        closed = res is not None and str(res.get("status")) == "CLOSED"
        row: dict[str, Any] = {
            "kind": "FOLLOWUP", "event": "ledger_result" if closed else "ledger_pending", "alert_id": w["alert_id"],
            "reply_to": w["alert_id"], "alert_sent": True, "status": w.get("status"),
            "strategy_id": w.get("strategy_id"), "symbol": w["symbol"], "side": w["side"],
            "timeframe": w["timeframe"], "event_at": now.isoformat(), "ledger": w["ledger"],
            "ledger_result": dict(res) if res else None, "position_id": w.get("position_id"), "text": text,
        }
        if self.delivering:
            reply_to = self.state["message_ids"].get(w["alert_id"])
            msg_id = self.sender.send(text, reply_to=reply_to) if self.sender else None
            row.update(code="SENT" if msg_id is not None else "SEND_FAILED", telegram_message_id=msg_id,
                       telegram_reply_to=reply_to)
        else:
            row["code"] = "DRY_RUN"
            self.preview.write(f"{now.isoformat()} · {w['ledger']} ledger follow-up · reply to {w['alert_id']} · "
                               f"{base_asset(str(w['symbol']))}", text)
        self._log(now, row)

    def ledger_trade(self, a: Mapping[str, Any]) -> dict[str, Any] | None:
        """The paper ledger's trade for an opened alert: by position_id, else the book's trade on the same
        symbol and side opened from the signal bar's open to one bar after its close (nearest that close)."""
        led = None
        if a.get("position_id"):
            led = self.data.paper_trade(str(a["position_id"]))
        sources = LEDGER_SNIPPET_SOURCES.get(str(a.get("source")), [])
        if led is None and sources:
            close = utc(a["bar_close"])
            tf = timedelta(minutes=timeframe_minutes(str(a["timeframe"])))
            led = self.data.paper_trade_near(str(a["symbol"]), str(a["side"]), utc(a["signal_bar"]), close + tf,
                                             close, sources)
        return led

    def summary_alerts(self, alerts: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """The day's sent alerts, each opened paper-book one with its ledger result (read at summary time)."""
        out = []
        for a in alerts:
            a = dict(a)
            if a.get("paper_book") and a.get("opened") and str(a.get("source")) in FWD_LEDGER_NAMES:
                # Owner ruling D150: a FWD-1 / FWD-3 trade's result from its own ledger (read only).
                a["fwd_ledger"] = FWD_LEDGER_NAMES[str(a["source"])]
                try:
                    a["ledger"] = self.ledger_result(a)
                except Exception as exc:  # noqa: BLE001 - the summary still goes out, marked
                    a["ledger"], a["ledger_error"] = None, type(exc).__name__
            elif a.get("paper_book") and a.get("opened"):
                try:
                    t = self.ledger_trade(a)
                except Exception as exc:  # noqa: BLE001 - the summary still goes out, marked
                    t, a["ledger_error"] = None, type(exc).__name__
                a["ledger"] = None if t is None else {
                    k: (t.get(k).isoformat() if isinstance(t.get(k), datetime) else t.get(k))
                    for k in ("id", "status", "exit_reason", "r_multiple", "pnl_usd", "entry_price", "exit_price",
                              "opened_at", "closed_at")
                }
            out.append(a)
        return out

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
        if self.state.get("ledger_watch"):
            n += self.update_ledger_followups(now)
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
        if sent and tr.get("ledger_result"):
            # v4: this alert's result comes from its ledger (D149/D150); the alert-level reply is only logged.
            row.update(code=LEVELS_ONLY, ledger=tr["ledger_result"])
            if ev["kind"] in ("final", "expired"):
                w = (self.state.get("ledger_watch") or {}).get(tr["alert_id"])
                if w is not None:
                    w["tracker_done"] = True
            if not self.delivering:
                self.preview.write(f"{when} · follow-up, alert levels only (not sent; result from the "
                                   f"{tr['ledger_result']} ledger) · reply to {tr['alert_id']} · "
                                   f"{base_asset(tr['symbol'])}", text)
        elif sent and self.delivering:
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
            d = day.isoformat()
            out.append(self.emit_summary(d, self.state["stats"].get(d, {}), now,
                                         alerts=self.state.setdefault("summary_alerts", {}).get(d, [])))
            self.state["last_summary_day"] = d
            self.state["summary_alerts"].pop(d, None)
            day += timedelta(days=1)
        return out

    def emit_summary(self, label: str, groups: Mapping[str, Any], now: datetime, *, title: str = "DAILY SUMMARY",
                     alerts: list[Mapping[str, Any]] | None = None) -> str:
        rows_ = self.summary_alerts(list(alerts or []))
        text = render_summary(label, groups, title=title, alerts=rows_)
        row: dict[str, Any] = {"kind": "SUMMARY", "day": label, "groups": groups, "alerts": rows_, "text": text}
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
            "span_frac", "cost_floor_frac", "cost_band", "cost_skip", "cost_stop_entry_ref", "cost_stop_entry_price",
            "cost_stop_frac", "cost_stop_round_trip", "cost_stop_floor_frac", "cost_stop_skip", "rounded")
    return {k: plan[k] for k in keys}


def all_summary_alerts(state: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Every day's recorded sent alerts, oldest day first (the backfill summary)."""
    days = state.get("summary_alerts") or {}
    return [a for d in sorted(days) for a in days[d]]


def merge_stats(stats: Mapping[str, Mapping[str, Mapping[str, float]]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for groups in stats.values():
        for name, g in groups.items():
            acc = out.setdefault(name, {"sent": 0, "not_sent": 0, "filled": 0, "wins": 0, "losses": 0, "total_r": 0.0})
            for k, v in g.items():
                acc[k] = acc.get(k, 0) + v
    return out
