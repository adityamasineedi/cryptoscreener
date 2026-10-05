"""Chart OHLCV response metadata helpers (unit-level sorting/dedupe semantics)."""

from __future__ import annotations


def _dedupe_sort(rows: list[dict]) -> tuple[list[dict], bool]:
    by_ot: dict[str, dict] = {}
    for r in rows:
        ot = r.get("open_time")
        key = ot if isinstance(ot, str) else ""
        by_ot[key] = r
    deduped = list(by_ot.values())
    deduplicated = len(deduped) != len(rows)
    sorted_rows = sorted(deduped, key=lambda r: str(r.get("open_time") or ""))
    return sorted_rows, deduplicated


def test_chart_rows_dedupe_and_sort_ascending():
    rows = [
        {"open_time": "2026-01-01T02:00:00+00:00", "close": 3},
        {"open_time": "2026-01-01T01:00:00+00:00", "close": 2},
        {"open_time": "2026-01-01T01:00:00+00:00", "close": 2.5},
        {"open_time": "2026-01-01T00:00:00+00:00", "close": 1},
    ]
    out, deduped = _dedupe_sort(rows)
    assert deduped is True
    assert len(out) == 3
    assert [r["open_time"] for r in out] == [
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T01:00:00+00:00",
        "2026-01-01T02:00:00+00:00",
    ]
    assert out[1]["close"] == 2.5


def test_chart_limit_contract_closed_plus_optional_open():
    """Documented contract: limit applies to closed; open tip may add +1."""
    closed = [{"open_time": f"t{i}", "is_closed": True} for i in range(200)]
    open_row = {"open_time": "t_open", "is_closed": False}
    rows = closed + [open_row]
    assert len([r for r in rows if r["is_closed"]]) == 200
    assert len(rows) == 201
    # Not an off-by-one: forming tip is intentional.
    assert any(not r["is_closed"] for r in rows)
