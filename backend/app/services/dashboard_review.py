"""Read-only snapshot for the paper dashboard.

Opens nothing, writes nothing, and does not construct the paper engine.
Callers pass paths so tests never read the live book.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.research.passed_cap_book import LARGE, MID, PASSED_SIDES, cap_group
from app.research.v1_production import V1_BOOKS, normalize_symbol

RESTART_UTC = datetime(2026, 10, 21, 3, 48, tzinfo=timezone.utc)
FRESH_SECONDS = 90 * 60


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _tail_json(path: Path) -> dict[str, Any] | None:
    rows = _read_jsonl(path)
    return rows[-1] if rows else None


def missed_bucket(reason: str | None) -> str:
    """Map a retained miss reason onto the dashboard buckets."""
    text = str(reason or "").lower()
    if text in {"late_tip", "late_bar"} or "late" in text:
        return "late bar"
    if "ohlcv_catchup" in text or "catchup" in text or text == "history_short":
        return "ohlcv_catchup"
    if "sleep" in text or "asleep" in text:
        return "sleep"
    if "crash" in text:
        return "crash"
    return "other"


def missed_breakdown(
    journal_rows: list[dict[str, Any]],
    watermark: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Split retained missed rows. The integer counter has no per-reason history."""
    wall = now or datetime.now(timezone.utc)
    mark = watermark or {}
    recent = [r for r in (mark.get("recent_missed") or []) if isinstance(r, dict)]
    journal_miss = [
        r
        for r in journal_rows
        if r.get("book") == "PASSED_BOOK"
        and r.get("event") == "signal"
        and str(r.get("outcome") or "") in {"missed", "not_evaluated"}
    ]
    # The watermark deque is the longer retained window. Journal rows that are
    # already in it are not counted twice.
    seen = {
        (
            str(r.get("symbol") or ""),
            str(r.get("setup_bar_time") or ""),
            str(r.get("reason") or ""),
            str(r.get("strategy_id") or ""),
            str(r.get("recorded_at") or ""),
        )
        for r in recent
    }
    retained = list(recent)
    for row in journal_miss:
        key = (
            str(row.get("symbol") or ""),
            str(row.get("setup_bar_time") or ""),
            str(row.get("reason") or ""),
            str(row.get("strategy_id") or ""),
            str(row.get("recorded_at") or row.get("ts") or ""),
        )
        if key not in seen:
            retained.append(row)
            seen.add(key)

    by_reason: dict[str, int] = dict(Counter(missed_bucket(r.get("reason")) for r in retained))
    for name in ("sleep", "crash", "late bar", "ohlcv_catchup", "other"):
        by_reason.setdefault(name, 0)
    by_book: dict[str, int] = dict(Counter(str(r.get("book") or r.get("strategy_id") or "PASSED_BOOK") for r in retained))
    by_day: dict[str, int] = defaultdict(int)
    after = []
    for row in retained:
        when = _utc(row.get("recorded_at") or row.get("ts"))
        day = when.date().isoformat() if when is not None else "unknown"
        by_day[day] += 1
        if when is not None and when >= RESTART_UTC:
            after.append(
                {
                    "recorded_at": when.isoformat(),
                    "symbol": row.get("symbol"),
                    "reason": row.get("reason"),
                    "bucket": missed_bucket(row.get("reason")),
                    "strategy_id": row.get("strategy_id"),
                    "setup_bar_time": row.get("setup_bar_time"),
                }
            )
    counter = int(mark.get("missed_signals") or 0)
    return {
        "counter": counter,
        "unevaluated_bars": int(mark.get("unevaluated_bars") or 0),
        "retained_rows": len(retained),
        "unretained": max(0, counter - sum(1 for r in retained if missed_bucket(r.get("reason")) == "late bar")),
        "by_reason": by_reason,
        "by_book": by_book,
        "by_day": dict(sorted(by_day.items())),
        "after_restart_utc": RESTART_UTC.isoformat(),
        "after_restart": after,
        "after_restart_unknown": [
            row for row in after if row["bucket"] == "other"
        ],
        "note": (
            "missed_signals counts late_tip only. The file keeps the newest "
            "recent_missed rows, not the whole  counter. history_short is an "
            "unevaluated bar, kept in the same recent list."
        ).replace("  ", " "),
        "as_of": wall.isoformat(),
    }


def _coins(symbols: tuple[str, ...] | list[str], risk: str) -> dict[str, Any]:
    return {"symbols": list(symbols), "risk": risk}


def _runner(name: str, at: datetime | None, *, now: datetime, detail: str) -> dict[str, Any]:
    if at is None:
        return {
            "runner": name,
            "last_heartbeat": None,
            "age_seconds": None,
            "status": "NO HEARTBEAT",
            "detail": detail,
        }
    age = max(0.0, (now - at).total_seconds())
    return {
        "runner": name,
        "last_heartbeat": at.isoformat(),
        "age_seconds": round(age, 1),
        "status": "FRESH" if age <= FRESH_SECONDS else "STALE",
        "detail": detail,
    }


def _stop_panel(engine: Any, journal: list[dict[str, Any]], start: dict[str, Any] | None) -> dict[str, Any]:
    limits = {}
    books: dict[str, Any] = {}
    if engine is not None:
        policy = engine.risk_policy
        limits = {
            "consecutive_loss_halt": int(policy.consecutive_loss_halt),
            "consecutive_loss_symbol_halt": int(policy.consecutive_loss_symbol_halt),
            "peak_drawdown_halt_pct": float(policy.peak_drawdown_halt_pct),
            "strategy_drawdown_halt_r": float(policy.strategy_drawdown_halt_r),
            "daily_loss_halt_r": float(policy.daily_loss_halt_r),
        }
        try:
            view = engine.halt_state()
        except Exception:  # noqa: BLE001
            view = {}
        counters = getattr(engine, "_stop_books", {}) or {}
        names = sorted(set(counters) | {str(k).split(":", 1)[0] for k in view if ":" in str(k)})
        if not names:
            names = sorted({str(k).split(":")[0] for k in view})
        for name in names or ["LEGACY"]:
            block = counters.get(name)
            halted = []
            paused = []
            for key, row in view.items():
                label = str(key)
                prefix = f"{name}:"
                bare = label[len(prefix):] if label.startswith(prefix) else (label if name == "LEGACY" else None)
                if bare is None:
                    continue
                item = {"halt": bare, "tripped_at": row.get("tripped_at"), "resets_at": row.get("resets_at")}
                if bare.startswith("symbol:"):
                    paused.append({**item, "symbol": bare.split(":", 1)[1]})
                else:
                    halted.append(item)
            equity = float(block.equity) if block is not None else None
            peak = float(block.peak_equity) if block is not None else None
            dd = ((peak - equity) / peak) if peak and equity is not None and peak > 0 else None
            strat = None
            if block is not None:
                strat = float(block.strategy_equity_r) - float(block.strategy_peak_r)
            books[name] = {
                "halted": bool(halted),
                "halts": halted,
                "paused_coins": paused,
                "consecutive_losses": int(block.consecutive_losses) if block is not None else None,
                "symbol_streaks": dict(block.symbol_consecutive_losses) if block is not None else {},
                "drawdown_pct": dd,
                "strategy_drawdown_r": strat,
                "day_pnl_r": float(block.day_pnl_r) if block is not None else None,
                "equity": equity,
            }
    return {
        "counter_start": start,
        "limits": limits,
        "books": books,
        "journal_tail": journal[-8:],
        "engine_loaded": engine is not None,
    }


def _candle12_summary(path: Path) -> dict[str, Any]:
    raw = _read_json(path)
    if not isinstance(raw, dict):
        return {"present": False, "equity": None, "realized_pnl": None, "net_r": None, "trades": 0, "open_risk": None}
    closed = [r for r in (raw.get("closed") or []) if isinstance(r, dict)]
    risk = float(raw.get("risk_usd") or 0.0)
    pnl = 0.0
    net_r = 0.0
    for row in closed:
        # candle12_v2_paper books closed trades with net_pnl_usd and no R field: R = net PnL / the book's risk.
        row_pnl = float(row.get("net_pnl_usd") or row.get("net_pnl") or row.get("pnl_usd") or 0.0)
        pnl += row_pnl
        r = row.get("r_multiple") if row.get("r_multiple") is not None else row.get("r_net")
        net_r += float(r) if r is not None else (row_pnl / risk if risk > 0 else 0.0)
    open_row = raw.get("open") if isinstance(raw.get("open"), dict) else None
    # The watermark is the forward start (fixed); the state file is rewritten after every new closed bar,
    # so its modification time is the runner's heartbeat.
    try:
        written = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        written = None
    return {
        "present": True,
        "symbol": raw.get("symbol"),
        "risk_usd": raw.get("risk_usd"),
        "account_equity": raw.get("account_equity"),
        "equity": float(raw.get("account_equity") or 0.0) + pnl,
        "realized_pnl": pnl,
        "net_r": net_r,
        "trades": len(closed),
        "open_risk": float(raw.get("risk_usd") or 0.0) if open_row else 0.0,
        "watermark": raw.get("watermark"),
        "written_at": written.isoformat() if written else None,
    }


def v1_watcher_symbols(settings: Any) -> frozenset[str]:
    """The V1 watcher's symbol filter, parsed as orchestrator.py and V1PaperWatcher parse it."""
    raw = str(getattr(settings, "paper_v1_watcher_symbols", "") or "")
    return frozenset(normalize_symbol(s) for s in raw.split(",") if s.strip())


def build_dashboard_review(
    *,
    root: Path | None = None,
    settings: Any | None = None,
    engine: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the display snapshot. ``engine`` must already exist; this does not create one."""
    base = root or ROOT
    wall = now or datetime.now(timezone.utc)
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    paper = base / "data" / "paper"
    journal = _read_jsonl(paper / "decision_journal.jsonl")
    watermark = _read_json(paper / "passed_paper_watermark.json")
    if not isinstance(watermark, dict):
        watermark = {}
    start = _read_json(paper / "stop_counters_start.json")
    halt_journal = _read_jsonl(paper / "halt_journal.jsonl")

    v1_open = bool(getattr(settings, "paper_v1_watcher_enabled", False))
    passed_open = bool(getattr(settings, "paper_passed_watcher_enabled", False))
    c12_open = bool(getattr(settings, "paper_candle12_v2_btc_long_enabled", False))
    book16_open = bool(getattr(settings, "paper_book16_4h_enabled", False))
    legacy_open = bool(getattr(settings, "paper_legacy_auto_entry_enabled", False))
    v21a_open = bool(getattr(settings, "paper_v21a_watcher_enabled", False))
    if engine is not None:
        legacy_open = bool(getattr(engine, "legacy_auto_entry_enabled", legacy_open))

    # Owner D200 (DECISIONS D233): list only the books the V1 watcher really runs. The
    # orchestrator passes paper_v1_watcher_symbols to the watcher, which keeps only those
    # symbols; an empty setting means every book, as in the watcher. Read from settings only.
    v1_symbols = v1_watcher_symbols(settings)
    v1_coins = [
        {"symbol": b.symbol, "timeframe": b.timeframe, "tier": b.tier, "risk_percent": b.risk_percent}
        for b in V1_BOOKS
        if b.timeframe == "1h" and b.enabled_by_default and (not v1_symbols or b.symbol in v1_symbols)
    ]
    passed_coins = [
        {"symbol": sym, "group": cap_group(sym), "risk_percent": 0.02 if cap_group(sym) == "large" else 0.01}
        for sym in (*LARGE, *MID)
    ]
    armed = []
    if v1_open:
        armed.append("V1")
    if passed_open:
        armed.append("PASSED")
    if c12_open:
        armed.append("CANDLE12_V2")
    if book16_open:
        armed.append("FWD-1")
    if legacy_open:
        armed.append("LEGACY")
    if v21a_open:
        armed.append("V21A")

    lock = base / "backend" / "scripts" / "lcsc_validation" / "forward_out" / "runner.lock"
    fwd3 = False
    fwd3_why = "No FWD-3 runner lock in this checkout."
    try:
        if lock.exists():
            age = wall.timestamp() - lock.stat().st_mtime
            fwd3 = age < 2 * 60 * 60
            fwd3_why = f"LCSC runner.lock age {int(age)}s."
    except OSError:
        fwd3_why = "FWD-3 lock could not be read."

    forward = [
        {"id": "passed-book parity test", "running": passed_open, "why": "PAPER_PASSED_WATCHER_ENABLED"},
        {"id": "V1", "running": v1_open, "why": "PAPER_V1_WATCHER_ENABLED"},
        {"id": "CANDLE12_V2", "running": c12_open, "why": "PAPER_CANDLE12_V2_BTC_LONG_ENABLED"},
        {"id": "FWD-1", "running": book16_open, "why": "PAPER_BOOK16_4H_ENABLED (BOOK16_4H_FWD-1 mirror)"},
        {"id": "FWD-3", "running": fwd3, "why": fwd3_why},
    ]

    tg_parts = []
    if bool(getattr(settings, "paper_v1_telegram_enabled", False)):
        tg_parts.append(f"COMBO_02 v1 ({len(v1_coins)} 1h books)")
    if bool(getattr(settings, "paper_v21a_telegram_enabled", False)):
        tg_parts.append("V2.1-A watcher")
    if legacy_open:
        tg_parts.append("legacy 15m")
    telegram_text = (
        "Telegram sends PAPER_ENTRY / PAPER_EXIT for " + ", ".join(tg_parts) + "."
        if tg_parts
        else "Telegram delivery flags are off. No watcher is set to send paper alerts."
    )

    oos = _tail_json(
        base / "backend" / "data" / "candle12_v3_paper" / "virtual_paper_mainnet" / "oos2" / "heartbeats.jsonl"
    )
    c12_state = _candle12_summary(base / "backend" / "data" / "candle12_v2_paper" / "btcusdt_long.json")
    book16 = _read_json(base / "backend" / "data" / "book16_paper" / "state.json")
    last_v1 = None
    for row in reversed(journal):
        if row.get("book") == "COMBO_02_V1":
            last_v1 = _utc(row.get("ts"))
            break
    runners = [
        _runner("passed book", _utc(watermark.get("updated_at")), now=wall, detail="passed_paper_watermark.json"),
        _runner("COMBO_02 v1", last_v1, now=wall, detail="last COMBO_02_V1 line in decision_journal.jsonl"),
        _runner(
            "CANDLE12_V2",
            _utc(c12_state.get("written_at")) if c12_state.get("present") else None,
            now=wall,
            detail="candle12_v2_paper/btcusdt_long.json last write (rewritten after each closed bar)",
        ),
        _runner(
            "FWD-1 book 16",
            _utc((book16 or {}).get("updated_utc")) if isinstance(book16, dict) else None,
            now=wall,
            detail="book16_paper/state.json",
        ),
        _runner(
            "CANDLE12_V3 OOS-2",
            _utc((oos or {}).get("at")),
            now=wall,
            detail="oos2/heartbeats.jsonl",
        ),
    ]
    return {
        "read_only": True,
        "as_of": wall.isoformat(),
        "armed_books": armed,
        "watchers": [
            {"id": "V1", "can_open": v1_open, "coins": v1_coins, "risk": "2% of equity on each 1h book"},
            {
                "id": "PASSED",
                "can_open": passed_open,
                "coins": passed_coins,
                "sides": [list(side) for side in PASSED_SIDES],
                "risk": "2% large-cap list, 1% mid-cap list, halved when the side is against the BTC daily trend",
            },
            {"id": "CANDLE12_V2", "can_open": c12_open, "coins": ["BTCUSDT"], "risk": "$20 on a $1,000 account"},
            {"id": "LEGACY", "can_open": legacy_open, "coins": [], "risk": "15m setup stream, off unless PAPER_LEGACY_AUTO_ENTRY_ENABLED"},
            {"id": "FWD-1", "can_open": book16_open, "coins": ["BTCUSDT", "ETHUSDT"], "risk": "0.5% of $10,000 per coin"},
            {"id": "V21A", "can_open": v21a_open, "coins": [], "risk": "separate watcher; off unless PAPER_V21A_WATCHER_ENABLED"},
        ],
        "telegram_text": telegram_text,
        "forward_tests": forward,
        "reset_blocked": any(row["running"] for row in forward),
        "stops": _stop_panel(engine, halt_journal, start if isinstance(start, dict) else None),
        "missed": missed_breakdown(journal, watermark, now=wall),
        "runners": runners,
        "candle12_v2": c12_state,
    }
