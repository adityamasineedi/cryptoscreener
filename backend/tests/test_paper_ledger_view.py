"""Owner D187: paper ledger VIEW. Evidence tags, default view and per-book stats (read-time only)."""

from __future__ import annotations

import copy
import shutil
import subprocess
from pathlib import Path

import pytest

from app.research import passed_symbol_allowlist as allowlist
from app.services import paper_ledger_view as view

PASSED = {"source": "PASSED_CAP_BOOK", "strategy_id": "COMBO_02_V2", "stream": "PASSED|COMBO_02_V2|SHORT", "direction": "SHORT"}
V1 = {"source": "V1_PAPER_WATCHER", "strategy_id": "COMBO_02_V1", "direction": "LONG"}


def row(rid: str, *, opened: str = "2026-10-21T10:00:05.250000+00:00", closed: str | None = "2026-10-21T14:00:00+00:00",
        status: str = "CLOSED", symbol: str = "SUIUSDT", side: str = "SHORT", candle: str | None = "2026-10-21T09:00:00+00:00",
        snippet: dict | None = None, timeframe: str = "1h", **extra) -> dict:
    out = {
        "id": rid,
        "symbol": symbol,
        "side": side,
        "status": status,
        "entry_price": 1.0,
        "stop_price": 1.02,
        "tp1_price": 0.96,
        "quantity": 500.0,
        "risk_usd": 10.0,
        "opened_at": opened,
        "closed_at": closed,
        "exit_price": 0.98,
        "exit_reason": "TP1",
        "pnl_usd": 10.0,
        "r_multiple": 1.0,
        "source_candle_ts": candle,
        "timeframe": timeframe,
        "signal_snippet": dict(PASSED if snippet is None else snippet),
    }
    out.update(extra)
    return out


def tag(rows: list[dict], rid: str) -> str | None:
    return view.evidence_tags(rows)[rid]["tag"]


# ---- LEGACY ---------------------------------------------------------------------------------


def test_legacy_stream_is_legacy():
    snip = {"source": "LEGACY_SETUP_SIGNAL", "strategy_id": "RESEARCH_15M"}
    assert tag([row("a", snippet=snip, timeframe="15m")], "a") == "LEGACY"
    assert tag([row("b", snippet={"source": "V21A_PAPER_WATCHER", "strategy_id": "COMBO_02_V2_1_A"})], "b") == "LEGACY"


def test_disarmed_passed_side_is_legacy():
    snip = {**PASSED, "strategy_id": "COMBO_02_V2_1B", "stream": "PASSED|COMBO_02_V2_1B|SHORT"}
    rows = [row("a", snippet=snip)]
    assert view.book_of(rows[0]) == "PASSED"
    assert tag(rows, "a") == "LEGACY"
    assert "PASSED_SIDES" in view.evidence_tags(rows)["a"]["reason"]


def test_passed_coin_off_the_cap_list_is_legacy():
    assert tag([row("a", symbol="PEPEUSDT")], "a") == "LEGACY"


def test_allowlist_blocked_coin_side_is_legacy():
    entry = allowlist.SymbolEntry(symbol="SUIUSDT", allowed=(("COMBO_01", "1h", "SHORT"),))
    allowlist.set_allowlist_for_tests(allowlist.SymbolAllowlist(entries={"SUIUSDT": entry}, source="test"))
    assert tag([row("a")], "a") == "LEGACY"
    ok = {**PASSED, "strategy_id": "COMBO_01", "stream": "PASSED|COMBO_01|SHORT"}
    assert tag([row("b", snippet=ok)], "b") == "VALID"


def test_v1_book_that_is_not_an_enabled_1h_book_is_legacy():
    assert tag([row("a", symbol="BTCUSDT", side="LONG", snippet=V1)], "a") == "VALID"
    assert tag([row("b", symbol="BTCUSDT", side="LONG", snippet=V1, timeframe="15m")], "b") == "LEGACY"


# ---- PRE_REWRITE / PRE_COUNTERS -------------------------------------------------------------


def test_pre_rewrite_cutoff_is_the_live_merge_second():
    assert view.REWRITE_UTC.isoformat() == "2026-10-17T12:16:47+00:00"
    rows = [
        row("a", opened="2026-10-17T12:16:46.999999+00:00", candle="2026-10-17T11:00:00+00:00"),
        row("b", opened="2026-10-17T12:16:47+00:00", candle="2026-10-17T12:00:00+00:00"),
    ]
    assert tag(rows, "a") == "PRE_REWRITE"
    assert tag(rows, "b") == "PRE_COUNTERS"


def test_pre_counters_uses_the_owner_date_not_the_first_run():
    assert view.COUNTERS_UTC.isoformat() == "2026-10-21T00:00:00+00:00"
    rows = [
        # After stop_counters_start.json first_run_utc (2026-10-20T17:57:21Z), still before the owner's date.
        row("a", opened="2026-10-20T18:05:31.5+00:00", candle="2026-10-20T17:00:00+00:00"),
        row("b", opened="2026-10-20T23:59:59.999999+00:00", candle="2026-10-20T22:00:00+00:00"),
        row("c", opened="2026-10-21T00:00:00+00:00", candle="2026-10-20T23:00:00+00:00"),
    ]
    assert [tag(rows, r) for r in "abc"] == ["PRE_COUNTERS", "PRE_COUNTERS", "VALID"]


# ---- CATCHUP_CLOSE --------------------------------------------------------------------------


def test_catchup_close_is_same_second_open_and_close():
    rows = [
        row("a", opened="2026-10-21T18:05:31.100000+00:00", closed="2026-10-21T18:05:31.900000+00:00"),
        row("b", opened="2026-10-21T19:05:31.900000+00:00", closed="2026-10-21T19:05:32.000000+00:00", candle="2026-10-21T18:00:00+00:00"),
    ]
    assert tag(rows, "a") == "CATCHUP_CLOSE"
    assert tag(rows, "b") == "VALID"


def test_cancelled_row_is_never_catchup_close():
    rows = [row("a", status="CANCELLED", exit_reason="unfilled", pnl_usd=0.0,
                opened="2026-10-21T18:05:31.1+00:00", closed="2026-10-21T18:05:31.2+00:00")]
    assert tag(rows, "a") == "VALID"


# ---- DUPLICATE_FILL -------------------------------------------------------------------------


def test_duplicate_fill_same_book_symbol_side_candle():
    other_stream = {**PASSED, "strategy_id": "COMBO_01", "stream": "PASSED|COMBO_01|SHORT"}
    rows = [
        row("first", opened="2026-10-21T10:00:05+00:00"),
        # Same book (PASSED), symbol, side and candle; another stream of the book and a later second.
        row("second", opened="2026-10-21T10:00:20+00:00", snippet=other_stream),
        row("other-side", opened="2026-10-21T10:00:30+00:00", side="LONG",
            snippet={**PASSED, "strategy_id": "COMBO_03_TRANSITION_B", "stream": "PASSED|COMBO_03_TRANSITION_B|LONG", "direction": "LONG"}),
        row("other-candle", opened="2026-10-21T11:00:05+00:00", candle="2026-10-21T10:00:00+00:00"),
    ]
    tags = view.evidence_tags(rows)
    assert tags["first"]["tag"] == "VALID"
    assert tags["second"]["tag"] == "DUPLICATE_FILL"
    assert "first" in tags["second"]["reason"]
    assert tags["other-side"]["tag"] == "VALID"
    assert tags["other-candle"]["tag"] == "VALID"


def test_duplicate_fill_needs_the_same_book():
    rows = [
        row("passed", symbol="BTCUSDT", side="LONG",
            snippet={**PASSED, "strategy_id": "COMBO_03_TRANSITION_B", "stream": "PASSED|COMBO_03_TRANSITION_B|LONG"}),
        row("v1", symbol="BTCUSDT", side="LONG", snippet=V1, opened="2026-10-21T10:00:09+00:00"),
    ]
    assert tag(rows, "v1") == "VALID"


def test_duplicate_fill_candle_matches_across_timestamp_spellings():
    rows = [
        row("a", candle="2026-10-21T09:00:00+00:00"),
        row("b", candle="2026-10-21T14:30:00+05:30", opened="2026-10-21T10:01:00+00:00"),
    ]
    assert tag(rows, "b") == "DUPLICATE_FILL"


def test_duplicate_fill_when_the_ledger_marked_it():
    rows = [row("a", status="CANCELLED", exit_reason="duplicate_fill", pnl_usd=0.0, candle="2026-10-21T08:00:00+00:00")]
    assert tag(rows, "a") == "DUPLICATE_FILL"


def test_duplicate_fill_falls_back_to_fill_bar_then_entry_fill():
    fill = {**PASSED, "fill_bar_time": "2026-10-21T10:00:00+00:00"}
    rows = [
        row("a", candle=None, snippet=fill),
        row("b", candle="", snippet=fill, opened="2026-10-21T10:00:30+00:00"),
        row("c", candle=None, opened="2026-10-21T12:00:01.2+00:00", symbol="ATOMUSDT"),
        row("d", candle=None, opened="2026-10-21T12:00:01.7+00:00", symbol="ATOMUSDT"),
        row("e", candle=None, opened="2026-10-21T12:00:02.1+00:00", symbol="ATOMUSDT"),
    ]
    tags = view.evidence_tags(rows)
    assert [tags[r]["tag"] for r in "abcde"] == ["VALID", "DUPLICATE_FILL", "VALID", "DUPLICATE_FILL", "VALID"]


def test_open_rows_are_untagged_but_count_as_earlier_rows():
    rows = [
        row("open", status="OPEN", closed=None, pnl_usd=None, r_multiple=None, exit_reason=None),
        row("later", opened="2026-10-21T10:00:40+00:00"),
    ]
    tags = view.evidence_tags(rows)
    assert tags["open"]["tag"] is None
    assert tags["later"]["tag"] == "DUPLICATE_FILL"


# ---- precedence -----------------------------------------------------------------------------


def test_precedence_first_match_wins_and_matches_list_every_rule():
    legacy_old = row("legacy-old", opened="2026-10-09T07:45:02+00:00",
                     snippet={"source": "LEGACY_SETUP_SIGNAL", "strategy_id": "RESEARCH_15M"}, timeframe="15m")
    old_catchup = row("old-catchup", opened="2026-10-20T18:05:31.5+00:00", closed="2026-10-20T18:05:31.9+00:00",
                      candle="2026-10-20T17:00:00+00:00")
    catchup_dup = row("catchup-dup", opened="2026-10-21T10:00:40.1+00:00", closed="2026-10-21T10:00:40.6+00:00")
    rows = [legacy_old, old_catchup, row("first"), catchup_dup]
    tags = view.evidence_tags(rows)
    assert tags["legacy-old"]["tag"] == "LEGACY"
    assert tags["legacy-old"]["matches"] == ["LEGACY", "PRE_REWRITE", "PRE_COUNTERS"]
    assert tags["old-catchup"]["tag"] == "PRE_COUNTERS"
    assert tags["old-catchup"]["matches"] == ["PRE_COUNTERS", "CATCHUP_CLOSE"]
    assert tags["catchup-dup"]["tag"] == "CATCHUP_CLOSE"
    assert tags["catchup-dup"]["matches"] == ["CATCHUP_CLOSE", "DUPLICATE_FILL"]
    assert view.TAGS == ("LEGACY", "PRE_REWRITE", "PRE_COUNTERS", "CATCHUP_CLOSE", "DUPLICATE_FILL", "VALID")


def test_every_closed_or_cancelled_row_gets_exactly_one_tag():
    rows = [
        row("a"),
        row("b", status="CANCELLED", exit_reason="duplicate_fill"),
        row("c", opened="2026-10-12T10:00:00+00:00", candle="2026-10-12T09:00:00+00:00"),
        row("d", status="OPEN", closed=None),
    ]
    out = view.build_ledger_view(rows)
    tags = {r["id"]: r["evidence_tag"] for r in out["rows"]}
    assert tags == {"a": "VALID", "b": "DUPLICATE_FILL", "c": "PRE_REWRITE", "d": None}
    assert sum(out["tag_counts"]["overall"].values()) == 3


# ---- default view and stats -----------------------------------------------------------------


def _book(out: dict, book: str) -> dict:
    return next(b for b in out["books"] if b["id"] == book)


def test_default_view_is_valid_rows_from_the_forward_start():
    rows = [
        row("old", opened="2026-10-20T10:00:00+00:00", closed="2026-10-20T12:00:00+00:00", candle="2026-10-20T09:00:00+00:00", pnl_usd=-50.0),
        row("valid", pnl_usd=20.0, risk_usd=10.0, r_multiple=2.0),
        row("dup", status="CANCELLED", exit_reason="duplicate_fill", pnl_usd=0.0, opened="2026-10-21T10:00:06+00:00"),
        row("legacy", snippet={"source": "LEGACY_SETUP_SIGNAL", "strategy_id": "RESEARCH_15M"}, timeframe="15m", pnl_usd=-99.0),
    ]
    out = view.build_ledger_view(rows)
    shown = [r["id"] for r in out["rows"] if r["in_default_view"]]
    assert shown == ["valid"]
    assert out["default_rows"] == 1 and out["total_rows"] == 4
    passed = _book(out, "PASSED")
    assert passed["forward_start"] == "2026-10-21T00:00:00+00:00"
    assert passed["trades"] == 1
    assert passed["pnl_usd"] == pytest.approx(20.0)
    assert passed["equity"] == pytest.approx(1020.0)
    assert _book(out, "LEGACY")["trades"] == 0 and _book(out, "LEGACY")["forward_start"] is None


def test_r_on_actual_risk_nominal_r_and_the_leverage_cap_count():
    # Equity at open = 1000 + PnL of every CLOSED row closed before it (old one: -85.05).
    old = row("old", opened="2026-10-19T10:00:00+00:00", closed="2026-10-20T03:00:00+00:00", candle="2026-10-19T09:00:00+00:00", pnl_usd=-85.05)
    capped = row("capped", symbol="BTCUSDT", side="LONG", snippet={**V1, "risk_percent": 0.02, "paper_execution": {"capped_by_leverage": True}},
                 closed="2026-10-22T02:17:26+00:00", risk_usd=13.4484, pnl_usd=-15.5518, r_multiple=-15.5518 / 13.4484)
    full = row("full", symbol="SOLUSDT", side="LONG", snippet={**V1, "risk_percent": 0.02, "paper_execution": {"capped_by_leverage": False}},
               opened="2026-10-21T15:00:47+00:00", closed="2026-10-22T01:42:22+00:00", risk_usd=18.299, pnl_usd=-19.7207, r_multiple=-19.7207 / 18.299)
    out = view.build_ledger_view([old, capped, full])
    v1 = _book(out, "V1")
    assert v1["trades"] == 2 and v1["capped"] == 1
    assert v1["r_actual"] == pytest.approx(-15.5518 / 13.4484 - 19.7207 / 18.299)
    assert v1["r_ledger"] == pytest.approx(v1["r_actual"])
    assert v1["r_nominal"] == pytest.approx(-15.5518 / 18.299 - 19.7207 / 18.299)
    by_id = {r["id"]: r for r in out["rows"]}
    assert by_id["capped"]["equity_at_open"] == pytest.approx(914.95)
    assert by_id["capped"]["nominal_risk_usd"] == pytest.approx(18.299)
    assert by_id["capped"]["capped_by_leverage"] is True


def test_nominal_risk_falls_back_to_risk_usd_without_risk_percent():
    out = view.build_ledger_view([row("a", pnl_usd=5.0, risk_usd=10.0)])
    passed = _book(out, "PASSED")
    assert passed["r_nominal"] == pytest.approx(0.5) and passed["nominal_fallback"] == 1


def test_view_never_modifies_its_input_rows():
    rows = [row("a"), row("b", status="CANCELLED", exit_reason="duplicate_fill"), row("c", status="OPEN", closed=None)]
    before = copy.deepcopy(rows)
    view.build_ledger_view(rows)
    view.evidence_tags(rows)
    assert rows == before


def test_snippet_as_json_text_like_the_database_returns():
    raw = row("a")
    raw["signal_snippet"] = '{"source": "PASSED_CAP_BOOK", "strategy_id": "COMBO_02_V2", "stream": "PASSED|COMBO_02_V2|SHORT"}'
    assert tag([raw], "a") == "VALID"


# ---- read path: one READ ONLY SELECT --------------------------------------------------------


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Conn:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.log.append("exit")

    async def execute(self, stmt, *args):
        self.log.append(str(stmt))
        return _Result(self.rows)

    async def rollback(self):
        self.log.append("rollback")


class _Engine:
    def __init__(self, rows):
        self.log: list[str] = []
        self.rows = rows

    def connect(self):
        return _Conn(self.log, self.rows)

    def begin(self):  # pragma: no cover - must not be used
        raise AssertionError("ledger view must not open a write transaction")


async def test_read_ledger_rows_is_one_read_only_select():
    engine = _Engine([{"id": "a"}])
    rows = await view.read_ledger_rows(engine)
    assert rows == [{"id": "a"}]
    assert engine.log[0] == "SET TRANSACTION READ ONLY"
    assert engine.log[1].startswith("SELECT ") and engine.log[1].endswith("FROM paper_trades ORDER BY id")
    assert engine.log[2:] == ["rollback", "exit"]
    for stmt in engine.log[:2]:
        for word in ("INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "TRUNCATE", "CREATE"):
            assert word not in stmt.upper()


async def test_route_returns_the_view_and_reports_a_failed_read(monkeypatch):
    from app.api import dashboard_review_routes as routes

    async def rows_ok(engine=None):
        return [row("a")]

    monkeypatch.setattr(view, "read_ledger_rows", rows_ok)
    out = await routes.paper_ledger_view()
    assert out["available"] is True and out["read_only"] is True
    assert out["rows"][0]["evidence_tag"] == "VALID"

    async def rows_fail(engine=None):
        raise RuntimeError("database not connected")

    monkeypatch.setattr(view, "read_ledger_rows", rows_fail)
    out = await routes.paper_ledger_view()
    assert out["available"] is False and out["rows"] == []
    assert "database not connected" in out["error"]


# ---- the paper engine is untouched by D187 --------------------------------------------------

BASE = "cb9dbe2"
REPO = Path(__file__).resolve().parents[2]
# The only paths the D187 change may touch: the view module, its read-only route, its tests and
# the dashboard (frontend) files.
D187_ALLOWED = {
    "backend/app/services/paper_ledger_view.py",
    "backend/app/api/dashboard_review_routes.py",
    "backend/tests/test_paper_ledger_view.py",
}
D187_ALLOWED_PREFIXES = ("frontend/src/",)
ENGINE_FILES = (
    "backend/app/services/paper_trade.py",
    "backend/app/services/paper_sizing.py",
    "backend/app/services/paper_risk.py",
    "backend/app/services/paper_classification.py",
    "backend/app/services/passed_paper_watcher.py",
    "backend/app/services/v1_paper_watcher.py",
    "backend/app/services/v21a_paper_watcher.py",
    "backend/app/services/v2_candidate_paper_watcher.py",
    "backend/app/services/candle12_v2_paper.py",
    "backend/app/services/book16_paper.py",
    "backend/app/services/persistence.py",
    "backend/app/services/database.py",
    "backend/app/services/decision_journal.py",
    "backend/app/engines/orchestrator.py",
    "backend/app/research/passed_cap_book.py",
    "backend/app/research/passed_symbol_allowlist.py",
    "backend/app/research/passed_symbol_allowlist.json",
    "backend/app/research/v1_production.py",
    "backend/app/api/routes.py",
    "backend/app/main.py",
    "backend/app/config.py",
)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=60)


def test_d187_leaves_the_paper_engine_byte_identical_to_cb9dbe2():
    if shutil.which("git") is None or _git("cat-file", "-e", f"{BASE}^{{commit}}").returncode != 0:
        pytest.skip("git or the D187 base commit is not available")
    subjects = _git("log", "--format=%s", f"{BASE}..HEAD").stdout.splitlines()
    if any("D187" not in s for s in subjects):
        pytest.skip("commits other than D187 are on top of cb9dbe2; this guard covers the D187 change only")
    for path in ENGINE_FILES:
        assert _git("cat-file", "-e", f"{BASE}:{path}").returncode == 0, path
    diff = _git("diff", "--quiet", BASE, "--", *ENGINE_FILES)
    assert diff.returncode == 0, _git("diff", "--stat", BASE, "--", *ENGINE_FILES).stdout
    changed = [p for p in _git("diff", "--name-only", BASE).stdout.splitlines() if p]
    stray = [p for p in changed if p not in D187_ALLOWED and not p.startswith(D187_ALLOWED_PREFIXES)]
    assert stray == [], stray
