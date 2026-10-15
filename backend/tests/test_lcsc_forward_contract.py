"""LCSC forward contract: drift guard, audited refreeze, grey-zone retirement, uptime and record integrity."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1] / "scripts" / "lcsc_validation"
sys.path.insert(0, str(HERE))

import forward as F  # noqa: E402

UTC = timezone.utc
AFTER = F.EARLIEST_EVALUATION + timedelta(days=1)


def frozen_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A copy of every fingerprinted file plus a contract document, frozen."""
    root = tmp_path / "repo"
    for rel in F.CODE_FILES:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(F.REPO / rel, root / rel)
    doc = root / "docs" / "contract.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("frozen contract text\n", encoding="utf-8")
    lock = root / "docs" / "lock.json"
    F.write_lock(lock, root=root, doc=doc)
    return root, doc, lock


def guard(root, doc, lock):
    return F.startup_guard(lock_path=lock, root=root, doc=doc)


# ---- drift guard ------------------------------------------------------------------------------


def test_matching_fingerprints_allow_startup(tmp_path):
    root, doc, lock = frozen_tree(tmp_path)
    ok = guard(root, doc, lock)
    assert ok["status"] == "OK" and ok["contract_id"] == F.CONTRACT_ID


def test_changed_code_blocks_startup(tmp_path):
    root, doc, lock = frozen_tree(tmp_path)
    with (root / "backend/scripts/lcsc_validation/study.py").open("a") as fh:
        fh.write("\n# edited\n")
    with pytest.raises(F.DriftBlocked) as exc:
        guard(root, doc, lock)
    assert exc.value.code == "CONFIG_DRIFT"
    assert exc.value.detail == ["code:backend/scripts/lcsc_validation/study.py"]


def test_changed_config_blocks_startup(tmp_path, monkeypatch):
    root, doc, lock = frozen_tree(tmp_path)
    monkeypatch.setattr(F, "KILL_MAX_DD_R", 14.0)
    with pytest.raises(F.DriftBlocked) as exc:
        guard(root, doc, lock)
    assert "config_fingerprint" in exc.value.detail


def test_changed_contract_blocks_startup(tmp_path):
    root, doc, lock = frozen_tree(tmp_path)
    doc.write_text("frozen contract text, quietly amended\n", encoding="utf-8")
    with pytest.raises(F.DriftBlocked) as exc:
        guard(root, doc, lock)
    assert exc.value.detail == ["contract_doc"]
    doc.write_text("frozen contract text\n", encoding="utf-8")
    body = json.loads(lock.read_text())
    body["terms"]["min_closed_trades"] = 20  # edit the lock without a refreeze
    lock.write_text(json.dumps(body))
    with pytest.raises(F.DriftBlocked) as exc:
        guard(root, doc, lock)
    assert "lock_tampered" in exc.value.detail
    lock.unlink()
    with pytest.raises(F.DriftBlocked) as exc:
        guard(root, doc, lock)
    assert exc.value.detail == ["lock_missing"]


def test_explicit_refreeze_creates_a_new_version_and_keeps_the_old_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "CONTRACT_ID", "TEST/FWD-1")
    root, doc, lock = frozen_tree(tmp_path)
    old = json.loads(lock.read_text())
    with pytest.raises(FileExistsError):
        F.write_lock(lock, root=root, doc=doc)  # never overwritten in place
    monkeypatch.setattr(F, "CONTRACT_ID", "TEST/FWD-2")
    doc.write_text("contract v2 text\n", encoding="utf-8")
    with pytest.raises(F.DriftBlocked):
        guard(root, doc, lock)
    hist = tmp_path / "history"
    before = F.FORWARD_START - timedelta(hours=1)
    with pytest.raises(ValueError):
        F.refreeze(new_version="TEST/FWD-2", reason=" ", by="me", lock_path=lock, history_dir=hist, root=root, doc=doc,
                   now=before)
    with pytest.raises(ValueError):
        F.refreeze(new_version="TEST/FWD-3", reason="r", by="me", lock_path=lock, history_dir=hist, root=root, doc=doc,
                   now=before)
    with pytest.raises(ValueError):
        F.refreeze(new_version="TEST/FWD-2", reason="r", by="me", lock_path=lock, history_dir=hist, root=root, doc=doc,
                   now=F.FORWARD_START)
    new = F.refreeze(new_version="TEST/FWD-2", reason="grey-zone rule added", by="reviewer", lock_path=lock,
                     history_dir=hist, root=root, doc=doc, now=before)
    assert guard(root, doc, lock)["contract_id"] == "TEST/FWD-2"
    archived = json.loads((hist / "TEST_FWD-1.lock.json").read_text())
    assert archived["status"] == "SUPERSEDED" and archived["superseded_by"] == "TEST/FWD-2"
    assert archived["lock"] == old and archived["reason"] == "grey-zone rule added"
    assert new["supersedes"]["contract_id"] == "TEST/FWD-1" and new["refreeze"]["by"] == "reviewer"
    log = [json.loads(x) for x in (hist / "refreeze_log.jsonl").read_text().splitlines()]
    assert log[0]["from"] == "TEST/FWD-1" and log[0]["to"] == "TEST/FWD-2" and log[0]["new_lock_hash"] == new["lock_hash"]
    with pytest.raises(ValueError):
        F.refreeze(new_version="TEST/FWD-2", reason="again", by="me", lock_path=lock, history_dir=hist, root=root,
                   doc=doc, now=before)


def test_diagnostic_mode_cannot_bypass_drift(tmp_path, monkeypatch):
    body = F.build_lock()
    body["config_fingerprint"] = "0" * 64
    body["lock_hash"] = F.lock_hash(body)
    lock = tmp_path / "drifted.lock.json"
    lock.write_text(json.dumps(body))
    monkeypatch.setattr(F, "LOCK_PATH", lock)
    monkeypatch.setattr(F, "OUT", tmp_path / "out")

    def no_data(now):
        raise AssertionError("forward data must not be read when the guard fails")

    monkeypatch.setattr(F, "load_forward_data", no_data)
    assert F.main(["evaluate", "--diagnostic"]) == 3
    assert F.main(["run"]) == 3
    assert F.main(["status"]) == 3


# ---- grey zone ----------------------------------------------------------------------------------


def trade(i, r, trend="TREND_UP"):
    t = datetime(2026, 11, 1, tzinfo=UTC) + timedelta(days=3 * i)
    return {"signal_time": t.isoformat(), "exit_time": (t + timedelta(hours=40)).isoformat(), "R": r,
            "net_usd": 20 * r, "trend": trend, "fees_usd": 1.0, "slippage_usd": 0.5, "funding_usd": -0.2}


def book(n, win, loss=-1.1):
    return [trade(i, win if i % 2 == 0 else loss, trend=F.TREND_REGIMES[i % 3]) for i in range(n)]


def ev(closed, now=AFTER, exceptions=()):
    return F.evaluate(closed, now=now, open_exceptions=list(exceptions), drift_list=[], data_fp="x")


def test_weak_positive_expectancy_continues():
    read = ev(book(40, 1.25))  # +0.075R, PF 1.14
    assert read["decision"] == "NO_PASS_CONTINUE" and read["acceptance"] is False
    assert read["note"] == F.GREY_ZONE_LANGUAGE
    assert "insufficient evidence, not evidence of failure" in read["note"]


def test_retired_at_80_trades():
    read = ev(book(80, 1.25))
    assert read["decision"] == "KILL_INSUFFICIENT_EDGE" and read["continuation_limit_reached"] is True
    assert ev(book(79, 1.25))["decision"] == "NO_PASS_CONTINUE"


def test_retired_at_18_months():
    assert ev(book(50, 1.25), now=F.CONTINUATION_MAX_TIME)["decision"] == "KILL_INSUFFICIENT_EDGE"
    assert ev(book(50, 1.25), now=F.CONTINUATION_MAX_TIME - timedelta(hours=1))["decision"] == "NO_PASS_CONTINUE"
    assert F.CONTINUATION_MAX_TIME == datetime(2028, 4, 16, tzinfo=UTC)


def test_kill_rules_take_precedence_over_grey_zone_and_retirement():
    assert ev(book(80, 1.0, -1.0))["decision"] == "KILL"  # expectancy 0 at the continuation limit
    losing_run = [trade(i, -1.6) for i in range(10)] + [trade(10 + i, 1.4 if i % 2 == 0 else -1.1,
                                                              trend=F.TREND_REGIMES[i % 3]) for i in range(40)]
    read = ev(losing_run)
    assert read["decision"] == "KILL" and "DRAWDOWN_ABOVE_15R" in read["kills"]


def test_strong_book_passes_even_at_the_limit_but_never_without_clearance():
    assert ev(book(80, 1.9))["decision"] == "PASS_TO_PAPER_ELIGIBLE"
    read = ev(book(40, 1.9), exceptions=[{"id": "FUNDING_GAP:2027-01-01T08:00:00+00:00"}])
    assert read["decision"] == "KILL" and read["passes"]["no_unresolved_exceptions"] is False
    assert read["acceptance"] is False


# ---- uptime checks -------------------------------------------------------------------------------


def bars(start, n, skip=None):
    return [{"time": start + timedelta(hours=k), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
             "volume": 1.0} for k in range(n) if k != skip]


def settlements(start, n, skip=None):
    return [{"t": int((start + timedelta(hours=8 * k)).timestamp() * 1000), "rate": 0.0001, "mark": None}
            for k in range(n) if k != skip]


def test_stale_candles_and_funding_are_exceptions():
    now = F.FORWARD_START + timedelta(days=2, hours=5, minutes=30)
    fresh = bars(F.FORWARD_START, 53)  # last bar 2 days 04:00, closed at 05:00
    funding = settlements(F.FORWARD_START, 7)  # last settlement day 2 00:00
    assert F.uptime_checks(fresh, funding, now=now) == []
    stale = F.uptime_checks(bars(F.FORWARD_START, 48), settlements(F.FORWARD_START, 5), now=now)
    assert {e["category"] for e in stale} == {"STALE_CANDLES", "STALE_FUNDING"}
    assert F.uptime_checks([], [], now=F.FORWARD_START - timedelta(days=1)) == []


def test_missing_candles_and_settlements_are_exceptions():
    start = F.FORWARD_START - timedelta(hours=40)
    s = F.build_series(bars(start, 80, skip=50), settlements(start, 10, skip=7), forward_start=F.FORWARD_START)
    found = {e["category"] for e in F.data_checks(s, settlements(start, 10, skip=7), F.FORWARD_START)}
    assert found == {"DATA_GAP", "FUNDING_GAP"}


# ---- append-only records and crash recovery ------------------------------------------------------


def test_record_chain_detects_edits_deletions_and_torn_writes(tmp_path):
    store = F.Store(tmp_path)
    for k in range(3):
        store.add_run({"as_of": f"2026-10-{16 + k}", "closed_trades": []})
    assert store.verify() == []
    path = tmp_path / "runs.jsonl"
    lines = path.read_text().splitlines()
    edited = json.loads(lines[1])
    edited["as_of"] = "2026-10-30"
    path.write_text("\n".join([lines[0], json.dumps(edited, sort_keys=True), lines[2]]) + "\n")
    assert any("row edited" in p for p in store.verify())
    path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    assert any("chain broken" in p for p in store.verify())
    path.write_text("\n".join(lines) + "\n" + lines[2][:20])
    assert any("torn write" in p for p in store.verify())


def test_crashed_run_is_recorded_and_concurrent_runs_are_refused(tmp_path):
    store = F.Store(tmp_path)
    now = datetime(2026, 10, 20, tzinfo=UTC)
    store.acquire(now)
    with pytest.raises(F.ConcurrentRun):
        F.Store(tmp_path).acquire(now + timedelta(minutes=10))
    later = F.Store(tmp_path)
    later.acquire(now + timedelta(hours=3))  # the first run never released its lock
    assert [e["category"] for e in later.open_exceptions()] == ["RUNNER_CRASH"]
    later.release()
    assert not (tmp_path / "runner.lock").exists()
    assert later.verify() == []


# ---- scheduled runs -------------------------------------------------------------------------------


def test_a_missed_scheduled_run_creates_an_uptime_exception():
    start = F.FORWARD_START
    on_time = {"as_of": (start + timedelta(hours=5, minutes=20)).isoformat()}
    assert F.missed_run_checks(on_time, now=start + timedelta(hours=6, minutes=20)) == []
    late = F.missed_run_checks(on_time, now=start + timedelta(hours=9, minutes=21))  # 4h01 since the last run
    assert [e["category"] for e in late] == ["RUN_MISSED"]
    assert late[0]["id"] == f"RUN_MISSED:{(start + timedelta(hours=5, minutes=20)).isoformat()}"
    # Nothing ran since the window opened.
    first = F.missed_run_checks({"as_of": (start - timedelta(hours=1)).isoformat()}, now=start + timedelta(hours=4))
    assert first[0]["id"] == f"RUN_MISSED:{start.isoformat()}"
    assert F.missed_run_checks(None, now=start - timedelta(hours=10)) == []  # before the window: not checked


def test_missed_run_exception_is_recorded_and_blocks_acceptance(tmp_path):
    store = F.Store(tmp_path)
    store.add_run({"as_of": (F.FORWARD_START + timedelta(hours=1)).isoformat(), "closed_trades": []})
    for e in F.missed_run_checks(store.last_run(), now=F.FORWARD_START + timedelta(days=2)):
        store.add_exception(e)
    assert [e["category"] for e in store.open_exceptions()] == ["RUN_MISSED"]
    read = F.evaluate(book(40, 1.9), now=AFTER, open_exceptions=store.open_exceptions(), drift_list=[], data_fp="x")
    assert read["decision"] == "KILL" and "UNRESOLVED_EXCEPTIONS" in read["kills"]


def test_verify_records_checks_a_backup_copy(tmp_path, monkeypatch):
    store = F.Store(tmp_path / "live")
    store.add_run({"as_of": "2026-10-16T00:20:00+00:00", "closed_trades": []})
    backup = tmp_path / "backup"
    shutil.copytree(tmp_path / "live", backup)
    assert F.main(["verify-records", "--path", str(backup)]) == 0
    (backup / "runs.jsonl").write_text((backup / "runs.jsonl").read_text().replace("00:20", "00:21"))
    assert F.main(["verify-records", "--path", str(backup)]) == 4
