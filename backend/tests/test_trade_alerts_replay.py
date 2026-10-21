"""Telegram trade alerts are output-only (owner message 2026-10-21, C2).

Replay: the passed-book watcher runs the same closed bars twice, alerts off and alerts fully on (delivery
included, through a mock transport: no network). The paper decisions, the opened positions and the
decision-journal rows must be identical. A static check also keeps every decision path from importing
the alert package.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app.alerts.config import AlertConfig, CONFIG_PATH
from app.alerts.data import MemoryData
from app.alerts.pipeline import AlertPipeline
from app.alerts.sender import ReplyingTelegramSender
from app.alerts.sources import journal_items, read_lines
from app.services import decision_journal
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperTradeEngine
from app.services.passed_paper_watcher import PassedPaperWatcher
from passed_paper_bars import exit_passed_on_bar

START = datetime(2026, 10, 1, tzinfo=timezone.utc)
VOLATILE = {"ts", "recorded_at", "position_id", "lag_seconds"}


def _candles(n: int) -> list[dict]:
    out = []
    for h in range(n):
        base = 100.0 + (h % 7) * 0.3
        out.append({"time": START + timedelta(hours=h), "open": base, "high": base + 1.2, "low": base - 1.1, "close": base + 0.2})
    return out


def _closed_4h(tip_time: datetime, n: int = 80) -> list[dict]:
    decision = tip_time + timedelta(hours=1)
    last = decision.replace(hour=decision.hour - decision.hour % 4) - timedelta(hours=4)
    return [{"time": last - timedelta(hours=4 * (n - 1 - i)), "open": 1, "high": 2, "low": 0.5, "close": 1} for i in range(n)]


def _fake_eval(**kwargs):
    combo = kwargs["combination"].combination_id
    candles = kwargs["candles"]
    tip = candles[kwargs["as_of_index"]]
    close = float(tip["close"])
    hour = int((tip["time"] - START).total_seconds() // 3600)
    if combo == "COMBO_01" and hour % 2 == 1:
        return {"status": "SHORT_ENTRY_CANDIDATE", "entry_price": close, "stop_price": close + 2.0, "tp1": close - 4.5}
    if combo == "COMBO_02_LOCAL" and hour % 3 == 0:
        return {"status": "SHORT_ENTRY_CANDIDATE", "entry_price": close, "stop_price": close + 1.5, "tp1": close - 3.5}
    return {"status": "NO_SETUP"}


def _strip(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in VOLATILE}


def _positions(eng: PaperTradeEngine) -> list[dict]:
    out = []
    for p in list(eng._open.values()) + list(getattr(eng, "_closed", []) or []):  # noqa: SLF001
        d = p.to_dict()
        out.append({k: d.get(k) for k in ("symbol", "side", "status", "entry_price", "stop_price", "tp1_price",
                                         "quantity", "risk_usd", "source_candle_ts", "timeframe")})
    return sorted(out, key=lambda d: (d["symbol"], d["source_candle_ts"] or ""))


def _run(tmp: Path, monkeypatch, *, alerts_on: bool, detail: bool = False) -> dict:
    monkeypatch.setattr("app.services.passed_paper_watcher.evaluate_combination_at_bar", _fake_eval)
    journal = tmp / "decision_journal.jsonl"
    decision_journal.configure(journal, signal_detail=detail)
    eng = PaperTradeEngine(enabled=True, risk_policy=PaperRiskPolicy(enabled=False))
    watcher = PassedPaperWatcher(enabled=True, paper_engine=eng)
    candles = _candles(262)
    sent: list[dict] = []
    pipe = None
    offset, nlines = 0, 0
    if alerts_on:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        raw.update(enabled=True, dry_run=False, allow_killed=True)  # the passed book is KILLED: allow it here
        cfg = AlertConfig(raw, ticks={"BTCUSDT": 0.1}, round_trip_cost=0.0001)

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})

        sender = ReplyingTelegramSender(bot_token="t", chat_id="123456789", timeout=1.0,
                                        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)))
        pipe = AlertPipeline(cfg, out_dir=tmp / "alerts", data=MemoryData(), mode="watch", sender=sender)
        assert pipe.delivering
    decisions = []
    for n in range(250, 262):
        window = candles[:n]
        opened = watcher.on_closed_1h(
            "BTCUSDT", candles_1h=window, candles_4h=_closed_4h(window[-1]["time"]), candles_15m=window,
            market_direction="BEARISH", force=True,
            next_bar={"time": window[-1]["time"] + timedelta(hours=1), "open": candles[n]["open"]},
        )
        decisions.append([(p.signal_snippet.get("strategy_id"), p.side, p.entry_price, p.stop_price) for p in opened])
        if n == 255:
            # Stop the open position out so later signals open again.
            for p in list(eng._open.values()):  # noqa: SLF001
                exit_passed_on_bar(eng, p.symbol, float(p.stop_price) + 0.5, window[-1]["time"] + timedelta(hours=1))
        if pipe is not None:
            # The sidecar reads only what live wrote: the journal, the paper positions and closed bars.
            pipe.data = MemoryData({("BTCUSDT", "1h"): candles[:n]},
                                   [dict(p.to_dict(), id=p.id) for p in eng._open.values()])  # noqa: SLF001
            lines, offset = read_lines(journal, offset)
            now = window[-1]["time"] + timedelta(hours=1, minutes=1)
            for item in journal_items(lines, first_line_no=nlines + 1, data=pipe.data, cfg=pipe.cfg, candle12_state=None):
                pipe.handle(item, now)
            nlines += len(lines)
            pipe.update_trackers(now)
    decision_journal.configure(None)
    rows = [_strip(json.loads(x)) for x in journal.read_text(encoding="utf-8").splitlines()]
    return {
        "decisions": decisions,
        "journal": rows,
        "positions": _positions(eng),
        "opened_count": watcher.opened_count,
        "last_skip": watcher.last_skip,
        "sent": sent,
        "alerts": (tmp / "alerts" / "alerts.jsonl").read_text(encoding="utf-8").splitlines() if alerts_on else [],
    }


@pytest.mark.usefixtures("passed_book_open")
def test_paper_decisions_identical_with_alerts_on_and_off(tmp_path, monkeypatch):
    off = _run(tmp_path / "off", monkeypatch, alerts_on=False)
    on = _run(tmp_path / "on", monkeypatch, alerts_on=True)
    for key in ("decisions", "journal", "positions", "opened_count", "last_skip"):
        assert on[key] == off[key], key
    # Byte for byte, once the wall-clock fields are dropped.
    assert json.dumps(on["journal"], sort_keys=True) == json.dumps(off["journal"], sort_keys=True)
    assert off["opened_count"] >= 1 and any(r.get("outcome") == "opened" for r in off["journal"])
    # The alert side really ran: alerts sent and linked to the journal rows that opened them.
    alerts = [json.loads(x) for x in on["alerts"]]
    sent_alerts = [a for a in alerts if a["kind"] == "ALERT" and a["code"] == "SENT"]
    assert sent_alerts and on["sent"]
    lines = (tmp_path / "on" / "decision_journal.jsonl").read_text(encoding="utf-8").splitlines()
    for a in sent_alerts:
        ref = a["journal_ref"]
        row = json.loads(lines[ref["line"] - 1])
        assert row["event"] == "signal" and row["outcome"] == "opened" and row["strategy_id"] == a["strategy_id"]


@pytest.mark.usefixtures("passed_book_open")
def test_with_signal_detail_not_opened_signals_alert_and_decisions_stay_identical(tmp_path, monkeypatch):
    """P14 journal detail on: alerts on/off still change nothing, and not-opened signals become alerts."""
    off = _run(tmp_path / "off", monkeypatch, alerts_on=False, detail=True)
    on = _run(tmp_path / "on", monkeypatch, alerts_on=True, detail=True)
    for key in ("decisions", "journal", "positions", "opened_count", "last_skip"):
        assert on[key] == off[key], key
    alerts = [json.loads(x) for x in on["alerts"]]
    sent = [a for a in alerts if a["kind"] == "ALERT" and a["code"] == "SENT"]
    not_opened = [a for a in sent if a["paper_outcome"] == "not_opened"]
    assert not_opened and all(a["paper_skip_reason"] for a in not_opened)
    assert {a["paper_outcome"] for a in sent} == {"opened", "not_opened"}
    assert not any(a["code"] == "NOT_OPENED" for a in alerts)
    lines = (tmp_path / "on" / "decision_journal.jsonl").read_text(encoding="utf-8").splitlines()
    for a in sent:
        row = json.loads(lines[a["journal_ref"]["line"] - 1])
        assert row["outcome"] == a["paper_outcome"] and row["stop_price"] == pytest.approx(a["plan"]["stop"])


def test_no_decision_path_imports_the_alert_package():
    app_dir = Path(__file__).resolve().parents[1] / "app"
    pat = re.compile(r"(from\s+app\.alerts|import\s+app\.alerts|from\s+app\s+import\s+[^\n]*\balerts\b)")
    offenders = []
    for py in app_dir.rglob("*.py"):
        if "alerts" in py.relative_to(app_dir).parts[:1]:
            continue
        if pat.search(py.read_text(encoding="utf-8", errors="replace")):
            offenders.append(str(py.relative_to(app_dir)))
    assert offenders == []


def test_alert_package_never_writes_the_journal_or_the_database():
    pkg = Path(__file__).resolve().parents[1] / "app" / "alerts"
    text = "\n".join(p.read_text(encoding="utf-8") for p in pkg.glob("*.py"))
    for call in ("import decision_journal", "decision_journal.write", "decision_journal.configure"):
        assert call not in text
    for sql in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE ", "CREATE ", "ALTER ", "DROP "):
        assert sql not in text.upper().replace("UPDATE_TRACKERS", "")
    assert "default_transaction_read_only" in text and "readonly=True" in text
