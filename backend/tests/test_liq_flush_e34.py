"""E34 LIQ-FLUSH recorder, signal, execution, freeze, and byte-identity."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from app.engines.liquidations.engine import parse_force_order
from app.research.daily_trend.books import atr_wilder as books_atr
from app.research.liq_flush import contract
from app.research.liq_flush.atr import atr_wilder
from app.research.liq_flush.book import Book, align_bars
from app.research.liq_flush.costs import size_qty, slippage
from app.research.liq_flush.execute import Bar, Position, try_exit
from app.research.liq_flush.ledger import (
    add_months,
    append_jsonl,
    judge,
    resolve_store,
    status_from_events,
)
from app.research.liq_flush.recorder import Recorder, ingest_message, load_hourly
from app.research.liq_flush.signal import lower_wick_ok, p99_threshold, percentile_linear, side_at, upper_wick_ok
from app.research.liq_flush.stream import stream_url
from app.research.liq_flush.universe import UNIVERSE
from app.research.passed_cap_book import cap_symbols

UTC = timezone.utc
LIVE = Path(r"D:\cryptoscreener")
WT = Path(__file__).resolve().parents[2]
ENGINE_FILES = (
    "backend/app/engines/liquidations/engine.py",
    "backend/app/engines/liquidations/__init__.py",
    "backend/app/ingestion/liquidations/force_order.py",
    "backend/app/ingestion/liquidations/provider.py",
    "backend/app/ingestion/liquidations/__init__.py",
)
PAPER_FILES = (
    "backend/app/services/paper_trade.py",
    "backend/app/services/passed_paper_watcher.py",
    "backend/app/services/v1_paper_watcher.py",
    "backend/app/services/v2_candidate_paper_watcher.py",
    "backend/app/services/v21a_paper_watcher.py",
    "backend/app/services/persistence.py",
    "backend/app/services/database.py",
    "backend/app/engines/orchestrator.py",
    "backend/app/research/passed_cap_book.py",
    "backend/app/research/passed_symbol_allowlist.py",
    "backend/app/research/passed_symbol_allowlist.json",
    "backend/app/research/v1_production.py",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hour(index: int, origin: datetime | None = None) -> datetime:
    base = origin or datetime(2026, 1, 1, tzinfo=UTC)
    return base + timedelta(hours=index)


def _force(symbol: str, side: str, price: str, qty: str, event_ms: int, *, average: str | None = None) -> dict:
    order = {"s": symbol, "S": side, "o": "LIMIT", "p": price, "q": qty, "X": "FILLED", "T": event_ms}
    if average is not None:
        order["ap"] = average
    return {"e": "forceOrder", "E": event_ms, "o": order}


def _quiet(hour: datetime) -> Bar:
    return Bar(hour, 100.0, 101.0, 99.5, 100.5)


def _flush(hour: datetime) -> Bar:
    return Bar(hour, 100.0, 100.4, 99.0, 100.2)


# ---------------------------------------------------------------- recorder


def test_sell_is_long_and_notional_is_price_times_qty(tmp_path: Path) -> None:
    rec = Recorder.open(tmp_path, production=False)
    start = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    rec.begin_session(start)
    event_ms = int(start.timestamp() * 1000) + 5_000
    assert ingest_message(rec, _force("ETHUSDT", "SELL", "10", "2", event_ms, average="999")) == 1
    assert ingest_message(rec, _force("ETHUSDT", "BUY", "4", "3", event_ms + 1000)) == 1
    assert ingest_message(rec, _force("BTCUSDT", "SELL", "100", "1", event_ms + 2000)) == 1
    parsed = parse_force_order(_force("ETHUSDT", "SELL", "10", "2", event_ms, average="999"))
    assert parsed is not None and parsed.notional == pytest.approx(1998)
    rec.seal_due(start + timedelta(hours=1, seconds=60))
    rows = load_hourly(tmp_path / "hourly.jsonl")
    assert rows["ETHUSDT"][start] == pytest.approx((20.0, 12.0))
    assert "BTCUSDT" not in rows
    assert rows["SOLUSDT"][start] == (0.0, 0.0)


def test_hour_bucket_dedup_and_append_once(tmp_path: Path) -> None:
    rec = Recorder.open(tmp_path, production=False)
    start = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    rec.begin_session(start)
    base = int(start.timestamp() * 1000)
    payload = _force("ETHUSDT", "SELL", "10", "1", base + 1_000)
    ingest_message(rec, payload)
    ingest_message(rec, payload)
    ingest_message(rec, _force("ETHUSDT", "SELL", "10", "1", base + 3_600_000))
    rec.seal_due(start + timedelta(hours=1, seconds=60))
    lines = (tmp_path / "hourly.jsonl").read_text(encoding="utf-8").splitlines()
    rec.seal_due(start + timedelta(hours=1, seconds=90))
    assert (tmp_path / "hourly.jsonl").read_text(encoding="utf-8").splitlines() == lines
    eth = load_hourly(tmp_path / "hourly.jsonl")["ETHUSDT"]
    assert eth[start][0] == pytest.approx(10.0)
    assert start + timedelta(hours=1) not in eth


def test_late_event_does_not_rewrite_a_sealed_hour(tmp_path: Path) -> None:
    rec = Recorder.open(tmp_path, production=False)
    start = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    rec.begin_session(start)
    rec.seal_due(start + timedelta(hours=1, seconds=60))
    before = (tmp_path / "hourly.jsonl").read_bytes()
    ingest_message(rec, _force("ETHUSDT", "SELL", "50", "1", int(start.timestamp() * 1000) + 10))
    rec.seal_due(start + timedelta(hours=1, seconds=120))
    assert (tmp_path / "hourly.jsonl").read_bytes() == before
    assert rec.late_dropped == 1


def test_partial_hour_and_restart_gap_are_absent(tmp_path: Path) -> None:
    rec = Recorder.open(tmp_path, production=False)
    rec.begin_session(datetime(2026, 6, 1, 10, 30, tzinfo=UTC))
    ten = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    eleven = ten + timedelta(hours=1)
    ingest_message(rec, _force("ETHUSDT", "SELL", "10", "1", int(ten.timestamp() * 1000) + 40 * 60_000))
    rec.seal_due(eleven + timedelta(seconds=60))
    assert load_hourly(tmp_path / "hourly.jsonl") == {}
    ingest_message(rec, _force("ETHUSDT", "BUY", "2", "5", int(eleven.timestamp() * 1000) + 1000))
    rec.seal_due(eleven + timedelta(hours=1, seconds=60))
    assert load_hourly(tmp_path / "hourly.jsonl")["ETHUSDT"][eleven] == pytest.approx((0.0, 10.0))

    restarted = Recorder.open(tmp_path, production=False)
    restarted.begin_session(datetime(2026, 6, 1, 13, 15, tzinfo=UTC))
    restarted.seal_due(datetime(2026, 6, 1, 15, 1, tzinfo=UTC))
    hours = set(load_hourly(tmp_path / "hourly.jsonl")["ETHUSDT"])
    assert eleven in hours
    assert datetime(2026, 6, 1, 12, 0, tzinfo=UTC) not in hours
    assert datetime(2026, 6, 1, 14, 0, tzinfo=UTC) in hours


def test_disconnect_hour_is_absent(tmp_path: Path) -> None:
    rec = Recorder.open(tmp_path, production=False)
    start = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    rec.begin_session(start)
    rec.note_disconnect(start + timedelta(minutes=5))
    rec.seal_due(start + timedelta(hours=1, seconds=60))
    assert load_hourly(tmp_path / "hourly.jsonl") == {}


def test_refuses_live_data_tree(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        resolve_store(Path(r"D:\cryptoscreener\data\liq_flush"), production=False)
    with pytest.raises(RuntimeError):
        resolve_store(Path(r"D:\cryptoscreener\data\paper\liq"), production=True)
    Recorder.open(tmp_path, production=False)
    assert not Path(r"D:\cryptoscreener\data\liq_flush").exists()


def test_stream_url_is_the_engine_stream() -> None:
    assert stream_url() == "wss://fstream.binance.com/market/ws/!forceOrder@arr"


# ---------------------------------------------------------------- signal


def test_percentile_matches_numpy_linear() -> None:
    values = [float(v) for v in range(1, 51)]
    assert percentile_linear(values, 99) == pytest.approx(float(np.percentile(values, 99, method="linear")))


def test_no_signal_before_30_days_or_without_648_hours() -> None:
    first = datetime(2026, 1, 1, tzinfo=UTC)
    too_soon = first + timedelta(hours=719)
    ready = first + timedelta(hours=720)
    assert p99_threshold([1.0] * 720, first_complete=first, bar_open=too_soon) is None
    assert p99_threshold([1.0] * 647, first_complete=first, bar_open=ready) is None
    assert p99_threshold([1.0] * 648, first_complete=first, bar_open=ready) == pytest.approx(1.0)


def test_wick_reclaim_and_both_sides() -> None:
    assert lower_wick_ok(100, 100.4, 99.0, 100.2)
    assert not lower_wick_ok(100, 101, 99.5, 100.5)
    assert upper_wick_ok(100, 102, 99.6, 100.2)
    first = datetime(2026, 1, 1, tzinfo=UTC)
    bar = first + timedelta(hours=720)
    prior = [1.0] * 720
    common = dict(
        long_prior=prior, short_prior=prior, first_long=first, first_short=first, bar_open=bar,
        open_=100.0, high=100.4, low=99.0, close=100.2, next_close=100.5,
    )
    assert side_at(long_now=1.0, short_now=1.0, **common) is None
    assert side_at(long_now=5.0, short_now=1.0, **common) == "LONG"
    assert side_at(long_now=5.0, short_now=1.0, **{**common, "next_close": 99.7}) is None
    # A doji can print a 50/50 wick. The next close is on one side of the midpoint, so only one side fires.
    doji = dict(
        long_prior=prior, short_prior=prior, first_long=first, first_short=first, bar_open=bar,
        open_=10.0, high=12.0, low=8.0, close=10.0, long_now=5.0, short_now=5.0,
    )
    assert upper_wick_ok(10, 12, 8, 10) and lower_wick_ok(10, 12, 8, 10)
    assert side_at(next_close=10.1, **doji) == "LONG"
    assert side_at(next_close=9.9, **doji) == "SHORT"
    assert side_at(next_close=10.0, **doji) is None


def test_atr_matches_the_research_wilder() -> None:
    rng = np.random.default_rng(34)
    h = np.cumsum(rng.normal(0, 1, 80)) + 100
    l = h - rng.uniform(0.2, 1.5, 80)
    c = l + rng.uniform(0, 1, 80) * (h - l)
    assert atr_wilder(h, l, c) == pytest.approx(books_atr(h, l, c, 14), nan_ok=True)


# ---------------------------------------------------------------- execution


def _pos(fill: float, stop: float, target: float, qty: float = 1.0) -> Position:
    hour = datetime(2026, 3, 1, tzinfo=UTC)
    return Position(
        "ETHUSDT", "LONG", 0, 0, hour, hour, fill, stop, target, qty, False, slippage("ETHUSDT"), 1.0,
    )


def test_two_touch_and_gap_stop() -> None:
    hour = datetime(2026, 3, 1, tzinfo=UTC)
    pos = _pos(100.02, 90.0, 120.0)
    both = try_exit(pos, [Bar(hour, 100.0, 130.0, 80.0, 100.0)], 0, [])
    assert both is not None and both.reason == "stop" and both.raw == 90.0
    assert both.exit_px == pytest.approx(90.0 * (1.0 - 0.0002))
    fees = 0.0005 * 1.0 * (100.02 + both.exit_px)
    assert both.fees == pytest.approx(fees)
    assert both.net == pytest.approx((both.exit_px - 100.02) - fees)

    gap = try_exit(pos, [Bar(hour, 80.0, 85.0, 79.0, 84.0)], 0, [])
    assert gap is not None and gap.reason == "stop_gap" and gap.raw == 80.0


def test_time_stop_is_bar_e_plus_47_and_includes_funding() -> None:
    start = datetime(2026, 3, 1, tzinfo=UTC)
    bars = [Bar(start + timedelta(hours=i), 100.0, 101.0, 99.0, 100.5) for i in range(48)]
    pos = _pos(100.02, 90.0, 130.0, qty=2.0)
    result = None
    settlement = int((start + timedelta(hours=8)).timestamp() * 1000)
    for i in range(48):
        result = try_exit(pos, bars, i, [(settlement, 0.0001)])
        if i < 47:
            assert result is None
    assert result is not None
    assert result.reason == "time" and result.exit_index == 47 and result.raw == 100.5
    assert result.funding == pytest.approx(-2.0 * 0.0001 * 100.5)


def test_cap_and_thin_slippage() -> None:
    qty, capped = size_qty(100.0, 99.95)
    assert capped and qty == pytest.approx(20.0)
    assert slippage("ETHUSDT") == 0.0002
    assert slippage("AAVEUSDT") == 0.0005


def test_short_mirror_stop_wins_on_two_touch() -> None:
    hour = datetime(2026, 3, 1, tzinfo=UTC)
    pos = Position(
        "ETHUSDT", "SHORT", 0, 0, hour, hour, 99.98, 110.0, 80.0, 1.0, False, 0.0002, 1.0,
    )
    result = try_exit(pos, [Bar(hour, 100.0, 111.0, 79.0, 100.0)], 0, [])
    assert result is not None and result.reason == "stop" and result.raw == 110.0
    assert result.exit_px == pytest.approx(110.0 * (1.0 + 0.0002))


# ---------------------------------------------------------------- book


def _install_start(store: Path, origin: datetime) -> Book:
    book = Book(store, production=False)
    append_jsonl(book.path, [{
        "at": "2026-06-01T00:00:00+00:00",
        "code_fingerprint": contract.fingerprint(),
        "contract_id": contract.CONTRACT_ID,
        "origin": origin.replace(microsecond=0).isoformat(),
        "type": "start",
        "universe": list(UNIVERSE),
    }])
    return book


def test_book_entries_exits_and_skips_signals_while_held(tmp_path: Path) -> None:
    origin = datetime(2026, 1, 1, tzinfo=UTC)
    n = 780
    flush_at = {720, 730, 769, 770}
    bars = [_flush(_hour(i, origin)) if i in flush_at else _quiet(_hour(i, origin)) for i in range(n)]
    liq = {
        "ETHUSDT": {
            _hour(i, origin): (1000.0 if i in flush_at else 1.0, 1.0)
            for i in range(n)
        }
    }
    book = _install_start(tmp_path, origin)
    book.advance({"ETHUSDT": bars}, {"ETHUSDT": []}, liq, origin + timedelta(hours=n))
    opens = [row["open"] for row in book.events() if row.get("type") == "step" and row.get("open")]
    closes = [row["close"] for row in book.events() if row.get("type") == "step" and row.get("close")]
    assert [row["signal_hour"] for row in opens] == [
        _hour(720, origin).isoformat(),
        _hour(770, origin).isoformat(),
    ]
    assert opens[0]["side"] == "LONG"
    assert opens[0]["entry_hour"] == _hour(722, origin).isoformat()
    assert closes[0]["reason"] == "time"
    assert closes[0]["exit_hour"] == _hour(722 + 47, origin).isoformat()
    assert opens[0]["stop"] == pytest.approx(bars[720].l - 0.2 * opens[0]["atr"])
    assert opens[0]["target"] == pytest.approx(opens[0]["fill"] + 2.0 * (opens[0]["fill"] - opens[0]["stop"]))
    again = book.advance({"ETHUSDT": bars}, {"ETHUSDT": []}, liq, origin + timedelta(hours=n))
    assert again == []


def test_invalid_stop_is_not_a_trade(tmp_path: Path) -> None:
    origin = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [_quiet(_hour(i, origin)) for i in range(20)]
    bars[14] = _flush(_hour(14, origin))
    bars[16] = Bar(_hour(16, origin), 1.0, 1.2, 0.8, 1.0)
    liq = {"ETHUSDT": {_hour(i, origin): (1000.0 if i == 14 else 1.0, 1.0) for i in range(800)}}
    # 30-day gate: pretend the first hour is far enough back by using only the threshold via a long history.
    first = origin - timedelta(hours=720)
    liq = {"ETHUSDT": {first + timedelta(hours=i): (1.0, 1.0) for i in range(720)}}
    liq["ETHUSDT"][_hour(14, origin)] = (1000.0, 1.0)
    book = _install_start(tmp_path, origin)
    book.advance({"ETHUSDT": bars}, {}, liq, origin + timedelta(hours=20))
    steps = [row for row in book.events() if row.get("type") == "step" and row.get("symbol") == "ETHUSDT"]
    assert any(row.get("reject") == "invalid_stop" for row in steps)
    assert not any(row.get("open") for row in steps)


def test_gap_halts(tmp_path: Path) -> None:
    origin = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [_quiet(_hour(i, origin)) for i in range(5)]
    del bars[2]
    book = _install_start(tmp_path, origin)
    book.advance({"ETHUSDT": bars}, {}, {}, origin + timedelta(hours=5))
    assert book.halted() == "DATA_GAP"
    aligned, gap = align_bars(origin, bars)
    assert gap == _hour(2, origin)
    assert len(aligned) == 2


def test_read_rule_withholds_pnl_until_30_and_then_judges() -> None:
    start = {"type": "start", "at": "2026-01-01T00:00:00+00:00"}
    assert status_from_events([start], datetime(2026, 2, 1, tzinfo=UTC))["read"] == "NOT_YET"
    assert "net" not in status_from_events([start], datetime(2026, 2, 1, tzinfo=UTC))
    later = status_from_events([start], datetime(2027, 1, 1, tzinfo=UTC))
    assert later["read"] == "INCONCLUSIVE" and "net" not in later
    closes = []
    for i in range(30):
        net = 3.0 if i % 5 else -1.0
        closes.append({
            "capped": i == 0,
            "exit_hour": (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=i)).isoformat(),
            "net": net,
            "side": "LONG",
            "symbol": "ETHUSDT",
            "trade_id": f"id-{i:02d}",
        })
    verdict = judge(closes)
    assert verdict["verdict"] == "PASS"
    assert verdict["net"] > 0 and verdict["pf"] > 1.10
    losing = [dict(row, net=-1.0) for row in closes]
    assert judge(losing)["verdict"] == "FAIL"
    assert add_months(datetime(2026, 1, 31, tzinfo=UTC), 1).month == 2


def test_universe_is_the_26_non_btc_cap_coins() -> None:
    assert UNIVERSE == tuple(sorted(symbol for symbol in cap_symbols() if symbol != "BTCUSDT"))
    assert len(UNIVERSE) == 26


# ---------------------------------------------------------------- freeze and byte identity


def test_worktree_matches_its_lock() -> None:
    assert contract.drift() == []


def test_drift_detects_an_edited_code_file(tmp_path: Path) -> None:
    for rel in contract.CODE_FILES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((WT / rel).read_bytes())
    lock_src = WT / "docs" / "liq_flush_e34_contract.lock.json"
    lock_dest = tmp_path / "docs" / "liq_flush_e34_contract.lock.json"
    lock_dest.write_bytes(lock_src.read_bytes())
    assert contract.drift(tmp_path) == []
    target = tmp_path / "backend" / "app" / "research" / "liq_flush" / "costs.py"
    target.write_bytes(target.read_bytes() + b"\n")
    assert "backend/app/research/liq_flush/costs.py" in contract.drift(tmp_path)


def test_frozen_paper_and_liquidation_files_match_live_head() -> None:
    lock = json.loads((WT / "docs" / "candle12_v3_oos_contract.lock.json").read_text(encoding="utf-8"))
    frozen = tuple(lock["code_fingerprint"]["files"])
    assert len(frozen) == 37
    for rel in frozen + PAPER_FILES + ENGINE_FILES:
        assert _sha(WT / rel) == _sha(LIVE / rel), rel
        if rel in lock["code_fingerprint"]["files"]:
            assert _sha(WT / rel) == lock["code_fingerprint"]["files"][rel], rel
