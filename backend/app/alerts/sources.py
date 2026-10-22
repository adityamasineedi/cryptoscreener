"""Turn live paper and forward-test records into alert signals. Read-only.

Sources:
- decision journal (``data/paper/decision_journal.jsonl``, written by live paper):
  - PASSED_BOOK ``signal`` rows: ``opened`` -> alert, stop and TP1 from that paper position
    (``paper_trades`` by ``position_id``, the engine's own values); ``missed`` -> alert row judged LATE;
    ``not_opened`` -> logged NOT_OPENED (no position, so no engine stop was recorded);
  - COMBO_02_V1 ``opened`` rows: stop from the row (the position's stop), TP1 from that paper position;
  - SCREENER ``signal`` rows (P14 detail): the engine's final stop and first target;
  - CANDLE12_V2 rows are not a source (owner ruling D120: the book is BTC-only, and BTC is for
    non-directional strategies only, D108). Its forward paper is not touched;
- BOOK16 FWD-1 ledger RUN rows (outside the journal): each new trade, stop from the trade, no fixed target;
- LCSC FWD-3 runs (outside the journal): each new candidate, stop = its invalidation, target from the
  contract's fixed R multiple off the signal close (config ``sources.lcsc_fwd3_target_r``);
- backfill only: paper positions opened before the journal existed (``paper_trades``), with no journal row.

Every paper-book signal carries its paper outcome in ``extra["live_outcome"]`` (owner ruling D145: only
"opened" alerts, see ``pipeline.not_opened_reason``). FWD-1 ledger trades and backfilled positions are
"opened". FWD-3: TAKEN is "opened"; PENDING_NEXT_BAR (the run's last closed bar, decided at the next bar's
open) is "opened" unless a trade of the same run is still open at that bar, which blocks it in the
contract's walk (forward.py ``walk_recorded``: the pending check comes before the open-trade check), then
it is "not_opened", reason "open_trade". The screener has no paper outcome.

Signal close and ATR always come from the closed bars of the signal timeframe (Postgres ``ohlcv``),
falling back to values carried by the record when the bar is missing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping

from app.alerts.config import AlertConfig
from app.alerts.data import MarketData
from app.alerts.fwd_ledgers import read_jsonl_lines, read_jsonl_readonly
from app.alerts.plan import Signal, atr_at, utc

BOOK_SOURCE = {"PASSED_CAP_BOOK": "PASSED_BOOK", "V1_PAPER_WATCHER": "COMBO_02_V1"}


@dataclass
class SkipRecord:
    """A journal signal that is not an alert (logged as a skip)."""

    code: str
    signal: Signal
    reason: str = ""


def line_ref(file_label: str, line_no: int, raw: str, row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "file": file_label,
        "line": int(line_no),
        "sha256": hashlib.sha256(raw.rstrip("\r\n").encode("utf-8")).hexdigest()[:16],
        "ts": row.get("ts"),
        "book": row.get("book"),
        "event": row.get("event"),
    }


def read_lines(path: Path, offset: int = 0) -> tuple[list[tuple[int, str]], int]:
    """Complete lines after byte ``offset``; returns (lines, new offset). A torn last line waits."""
    p = Path(path)
    if not p.exists():
        return [], offset
    with p.open("rb") as f:
        f.seek(offset)
        blob = f.read()
    end = blob.rfind(b"\n")
    if end < 0:
        return [], offset
    chunk = blob[: end + 1]
    return [(i, ln) for i, ln in enumerate(chunk.decode("utf-8").splitlines())], offset + len(chunk)


def enrich_market(sig: Signal, data: MarketData, cfg: AlertConfig) -> None:
    ez = cfg.section("entry_zone")
    try:
        bars = data.bars_upto(sig.symbol, sig.timeframe, sig.signal_bar, int(ez["atr_lookback_bars"]))
    except Exception as exc:  # noqa: BLE001
        sig.extra["market_error"] = str(exc)[:200]
        bars = []
    if bars and utc(bars[-1]["time"]) == sig.signal_bar:
        sig.signal_close = float(bars[-1]["close"])
        sig.atr = atr_at(bars, int(ez["atr_period"]))
        sig.extra["market_source"] = "ohlcv"
    else:
        sig.extra["market_source"] = "record" if sig.signal_close is not None else "missing"


# --- decision journal ---------------------------------------------------------------------------
def journal_items(
    lines: list[tuple[int, str]],
    *,
    first_line_no: int,
    data: MarketData,
    cfg: AlertConfig,
) -> Iterator[Signal | SkipRecord]:
    outcomes = set(cfg.raw.get("journal_alert_outcomes") or ["opened"])
    for i, raw in lines:
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        ref = line_ref("decision_journal.jsonl", first_line_no + i, raw, row)
        book, event = row.get("book"), row.get("event")
        try:
            item = _journal_item(book, event, row, ref, data, cfg, outcomes)
        except Exception as exc:  # noqa: BLE001 - one bad row never stops the sidecar
            row_err = f"{type(exc).__name__}: {str(exc)[:200]}"
            try:
                sig = Signal(source=str(book), strategy_id=str(row.get("strategy_id") or book),
                             symbol=str(row.get("symbol") or "?"), side=str(row.get("direction") or "LONG"),
                             timeframe=str(row.get("timeframe") or "1h"),
                             signal_bar=row.get("setup_bar_time") or row.get("bar") or row.get("ts"),
                             decided_at=row.get("ts"), journal_ref=ref, extra={"error": row_err})
            except Exception:  # noqa: BLE001
                continue
            item = SkipRecord("SOURCE_ERROR", sig, row_err)
        if item is not None:
            yield item


def _journal_item(book, event, row, ref, data, cfg, outcomes):  # noqa: ANN001
    """One journal row -> Signal, SkipRecord or None.

    P14 rows (``DECISION_JOURNAL_SIGNAL_DETAIL`` on) carry ``stop_price``, ``tp1`` and ``source``: then a signal
    that did not open alerts too, from the engine values in the row. Without them, not-opened signals stay
    NOT_OPENED skips, as before.
    """
    has_detail = row.get("detail_version") is not None
    if book == "PASSED_BOOK" and event == "signal" and row.get("strategy_id"):
        sig = Signal(
            source="PASSED_BOOK",
            strategy_id=str(row["strategy_id"]),
            symbol=str(row["symbol"]),
            side=str(row["direction"]),
            timeframe=str(row.get("timeframe") or "1h"),
            signal_bar=row["setup_bar_time"],
            decided_at=row.get("recorded_at") or row["ts"],
            journal_ref=ref,
            position_id=row.get("position_id") or None,
        )
        outcome = str(row.get("outcome") or "")
        sig.extra["live_outcome"] = outcome
        if row.get("reason"):
            sig.extra["skip_reason"] = str(row.get("reason"))
        if has_detail:
            sig.stop, sig.tested_target = _f(row.get("stop_price")), _f(row.get("tp1"))
            sig.extra["engine_values"] = "journal"
        if outcome == "missed":
            enrich_market(sig, data, cfg)
            return sig
        if outcome not in outcomes or (outcome != "opened" and not has_detail):
            return SkipRecord("NOT_OPENED", sig, f"{outcome}:{row.get('reason') or ''}")
        if not has_detail and sig.position_id:
            pos = _lookup(sig, data.paper_trade, sig.position_id)
            if pos:
                sig.stop = _f(pos.get("stop_price"))
                sig.tested_target = _f(pos.get("tp1_price"))
                sig.extra["engine_values"] = "paper_trades"
        enrich_market(sig, data, cfg)
        return sig
    if book == "COMBO_02_V1" and event == "opened":
        sig = Signal(
            source="COMBO_02_V1", strategy_id="COMBO_02_V1", symbol=str(row["symbol"]), side="LONG",
            timeframe="1h", signal_bar=row["bar"], decided_at=row["ts"], journal_ref=ref,
            stop=_f(row.get("stop_price")),
        )
        sig.extra["live_outcome"] = "opened"
        pos = _lookup(sig, data.v1_trade, sig.symbol, str(row["bar"]))
        if pos:
            sig.position_id = str(pos.get("id"))
            sig.tested_target = _f(pos.get("tp1_price"))
        enrich_market(sig, data, cfg)
        return sig
    if book == "COMBO_02_V1" and event == "signal" and has_detail:
        outcome = str(row.get("outcome") or "")
        if outcome == "opened":
            return None  # the "opened" row of the same decision is the alert
        sig = Signal(
            source="COMBO_02_V1", strategy_id="COMBO_02_V1", symbol=str(row["symbol"]), side="LONG",
            timeframe=str(row.get("timeframe") or "1h"), signal_bar=row["setup_bar_time"], decided_at=row["ts"],
            journal_ref=ref, stop=_f(row.get("stop_price")), tested_target=_f(row.get("tp1")),
            extra={"live_outcome": outcome, "skip_reason": str(row.get("reason") or ""), "engine_values": "journal"},
        )
        if outcome not in outcomes and outcome != "missed":
            return SkipRecord("NOT_OPENED", sig, f"{outcome}:{row.get('reason') or ''}")
        enrich_market(sig, data, cfg)
        return sig
    if book == "CANDLE12_V2":
        return None  # owner ruling D120: dropped from alerts (BTC-only book; BTC is non-directional only, D108)
    if book == "SCREENER" and event == "signal" and has_detail:
        sig = Signal(
            source="SCREENER", strategy_id=str(row.get("strategy_id") or "SCREENER_SETUP"), symbol=str(row["symbol"]),
            side=str(row["direction"]), timeframe=str(row["timeframe"]), signal_bar=row["setup_bar_time"],
            decided_at=row["ts"], journal_ref=ref, stop=_f(row.get("stop_price")), tested_target=_f(row.get("tp1")),
            target_tested=False,
            extra={"live_outcome": "screener_signal", "screener_status": row.get("status"), "engine_values": "journal",
                   "signal_entry_price": _f(row.get("signal_entry_price"))},
        )
        enrich_market(sig, data, cfg)
        return sig
    return None


def _lookup(sig: Signal, fn: Any, *args: Any) -> Any:
    """A read that fails leaves the values missing, so the row is deferred and retried."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001
        sig.extra["lookup_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return None


def awaiting_engine_values(item: "Signal | SkipRecord") -> bool:
    """A journal alert whose stop or tested target is not readable yet (written just after the row)."""
    if not isinstance(item, Signal) or item.extra.get("live_outcome") == "missed":
        return False
    if item.extra.get("engine_values") == "journal":
        return False  # P14 rows carry the engine values themselves
    return item.source in ("PASSED_BOOK", "COMBO_02_V1") and (item.stop is None or item.tested_target is None)


def _f(v: Any) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


# --- BOOK16 FWD-1 -------------------------------------------------------------------------------
def fwd1_items(
    path: Path, *, after_seq: int, data: MarketData, cfg: AlertConfig, seen: set[str], enrich: bool = True
) -> tuple[list[Signal], int]:
    """New trades in the FWD-1 ledger RUN rows after ``after_seq``. Returns (signals, last seq read)."""
    p = Path(path)
    if not p.exists():
        return [], after_seq
    out: list[Signal] = []
    last = after_seq
    rows, _ = read_jsonl_readonly(p)  # read only; a torn last line waits for the next poll (D150)
    for row in rows:
        seq = int(row.get("seq", -1))
        if seq <= after_seq:
            continue
        last = max(last, seq)
        if row.get("kind") != "RUN":
            continue
        for sym, book in ((row.get("payload") or {}).get("symbols") or {}).items():
            # The collector's RUN summary holds ``closed_trades`` as a COUNT (the trades are TRADE_CLOSED rows).
            trades = [book.get("open_position")] + _trade_list(book.get("closed_trades")) + _trade_list(book.get("pending_closed"))
            for t in trades:
                if not t or not t.get("entry_time"):
                    continue
                tid = str(t.get("trade_id") or f"{sym}|{t['entry_time']}")
                if tid in seen:
                    continue
                seen.add(tid)
                sig = Signal(
                    source="BOOK16_FWD1", strategy_id="BOOK16_4H", symbol=sym, side="LONG", timeframe="4h",
                    signal_bar=utc(t["entry_time"]) - timedelta(hours=4), decided_at=row["written_utc"],
                    journal_ref={"file": "book16_fwd1/ledger.jsonl", "seq": seq, "hash": str(row.get("hash"))[:16]},
                    stop=_f(t.get("stop_price")), tested_target=None, position_id=tid,
                    extra={"live_outcome": "opened"},  # a ledger trade with an entry time: FWD-1 opened it
                )
                if enrich:
                    enrich_market(sig, data, cfg)
                out.append(sig)
    return out, last


def _trade_list(v: Any) -> list[Any]:
    return [t for t in v if isinstance(t, Mapping)] if isinstance(v, list) else []


# --- LCSC FWD-3 ---------------------------------------------------------------------------------
FWD3_ALERT_FATES = {"TAKEN", "PENDING_NEXT_BAR"}


def fwd3_items(
    path: Path, *, after_line: int, data: MarketData, cfg: AlertConfig, seen: set[str], enrich: bool = True
) -> tuple[list[Signal | SkipRecord], int]:
    p = Path(path)
    if not p.exists():
        return [], after_line
    out: list[Signal | SkipRecord] = []
    # Read only. Complete lines only: a torn last line is not counted, so the next poll reads it (D150).
    lines = read_jsonl_lines(p)
    target_r = float(cfg.raw["sources"].get("lcsc_fwd3_target_r", 2.0))
    for n, row in enumerate(lines, start=1):
        if n <= after_line or row is None:
            continue
        for c in row.get("candidates") or []:
            st = str(c.get("signal_time") or "")
            if not st or st in seen:
                continue
            seen.add(st)
            close, stop = _f(c.get("close")), _f(c.get("invalidation"))
            sig = Signal(
                source="LCSC_FWD3", strategy_id="LCSC_V1", symbol="ETHUSDT", side="LONG", timeframe="1h",
                signal_bar=st, decided_at=row.get("run_at") or row.get("as_of"),
                journal_ref={"file": "lcsc_fwd3/runs.jsonl", "line": n, "row_hash": str(row.get("row_hash"))[:16]},
                stop=stop, signal_close=close, atr=_f(c.get("atr")), position_id=st,
            )
            # The contract's fixed target: target_r x (signal close - stop) above the signal close.
            if close is not None and stop is not None and close > stop:
                sig.tested_target = close + target_r * (close - stop)
            if enrich:
                enrich_market(sig, data, cfg)
            fate = str(c.get("fate") or "")
            sig.extra["fwd3_fate"] = fate
            if fate in FWD3_ALERT_FATES:
                if fate == "PENDING_NEXT_BAR" and _fwd3_open_trade_blocks(row, st):
                    sig.extra.update(live_outcome="not_opened", skip_reason="open_trade")
                else:
                    sig.extra["live_outcome"] = "opened"
                out.append(sig)
            else:
                out.append(SkipRecord("NOT_OPENED", sig, fate))
    return out, len(lines)


def _fwd3_open_trade_blocks(row: Mapping[str, Any], signal_time: str) -> bool:
    """True when a trade of this run, entered before the pending signal, has not exited before its bar.

    forward.py ``walk_recorded`` blocks a candidate while ``i < next_allowed`` (exit bar + 1, or the end of
    the data for an OPEN trade); an OPEN trade's ``exit_time`` is the run's last closed bar.
    """
    try:
        at = utc(signal_time)
    except ValueError:
        return False
    for t in row.get("trades") or []:
        try:
            if utc(t["signal_time"]) < at and utc(t["exit_time"]) >= at:
                return True
        except (KeyError, TypeError, ValueError):
            continue
    return False


# --- backfill: paper positions before the journal -----------------------------------------------
def paper_trade_items(rows: list[Mapping[str, Any]], *, data: MarketData, cfg: AlertConfig) -> list[Signal]:
    out = []
    for r in rows:
        snip = r.get("signal_snippet") or {}
        source = BOOK_SOURCE.get(str(snip.get("source") or ""))
        if source is None:
            continue
        sig = Signal(
            source=source,
            strategy_id=str(snip.get("strategy_id") or ("COMBO_02_V1" if source == "COMBO_02_V1" else "?")),
            symbol=str(r["symbol"]), side=str(r["side"]), timeframe=str(r.get("timeframe") or "1h"),
            signal_bar=r["source_candle_ts"], decided_at=r["opened_at"],
            journal_ref=None, position_id=str(r["id"]),
            stop=_f(r.get("stop_price")), tested_target=_f(r.get("tp1_price")),
            extra={"source_ref": f"paper_trades:{r['id']}", "note": "opened before the decision journal existed",
                   "live_outcome": "opened"},
        )
        enrich_market(sig, data, cfg)
        out.append(sig)
    return out


def first_journal_ts(path: Path) -> datetime | None:
    p = Path(path)
    if not p.exists():
        return None
    with p.open("r", encoding="utf-8") as f:
        first = f.readline()
    try:
        return utc(json.loads(first)["ts"])
    except (ValueError, KeyError):
        return None
