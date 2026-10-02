"""Tests for dynamic max-100 screen universe selection.

Presentation ranking only — does not change signal/structure/entry math.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.schemas import DataStatus, FreshValue, ScreenerRow
from app.services.screen_universe import (
    MAX_SCREEN_SYMBOLS,
    ScreenUniverseSelector,
    clamp_screen_limit,
    compute_screen_priority,
    hard_eligibility,
    matches_screen_filter,
)


def _fv(value, status=DataStatus.LIVE, source="test"):
    return FreshValue(
        value=value,
        timestamp=datetime.now(timezone.utc),
        source=source,
        status=status,
    )


def _row(
    symbol: str,
    *,
    price: float | None = 100.0,
    price_status: DataStatus = DataStatus.LIVE,
    vol: float = 1_000_000.0,
    setup: str = "WAITING",
    market: str = "WAITING",
    structure: str = "BULLISH",
    structure_status: DataStatus = DataStatus.LIVE,
    rvol: float | None = 1.2,
    rvol_status: DataStatus = DataStatus.LIVE,
    data_status: DataStatus = DataStatus.LIVE,
    bos: str | None = "BULLISH_BOS",
) -> ScreenerRow:
    waiting = FreshValue.waiting("test")
    return ScreenerRow(
        symbol=symbol,
        base_asset=symbol.replace("USDT", ""),
        quote_asset="USDT",
        market_type="futures_perp",
        price=_fv(price, price_status) if price is not None else FreshValue.unavailable("test"),
        change_24h_pct=_fv(1.0),
        volume_24h=_fv(vol),
        quote_volume_24h=_fv(vol),
        high_24h=_fv(price or 0),
        low_24h=_fv(price or 0),
        funding_rate=_fv(0.0001),
        open_interest=_fv(1000.0),
        oi_change_pct=_fv(0.1),
        market_cap=_fv(1e9),
        fdv=_fv(1e9),
        tvl=waiting,
        relative_volume=_fv(rvol, rvol_status) if rvol is not None else FreshValue.unavailable("test"),
        structure=_fv(structure, structure_status) if structure else FreshValue.waiting("test"),
        zone=_fv("DEMAND"),
        technical_state=_fv("NEUTRAL"),
        setup_signal=_fv(setup),
        market_signal=_fv(market),
        setup_trend=_fv(structure),
        setup_bos=_fv(bos) if bos else FreshValue.waiting("test"),
        setup_impulse=_fv("MODERATE"),
        setup_pullback=_fv("ACTIVE"),
        bos=_fv(bos) if bos else FreshValue.waiting("test"),
        data_status=data_status,
    )


def test_clamp_never_exceeds_100():
    assert clamp_screen_limit(1000) == 100
    assert clamp_screen_limit(100) == 100
    assert clamp_screen_limit(50) == 50
    assert clamp_screen_limit(25) == 25
    assert clamp_screen_limit(None) == 100
    assert MAX_SCREEN_SYMBOLS == 100


def test_528_eligible_returns_exactly_100():
    rows = [_row(f"S{i:03d}USDT", setup="NO_SETUP", market="NEUTRAL", vol=1e6 + i) for i in range(528)]
    sel = ScreenUniverseSelector(ttl_sec=0)
    result = sel.select(rows, total_universe=528, limit=100, force_refresh=True)
    assert result.returned_count == 100
    assert len(result.rows) == 100
    assert result.eligible_count == 528
    assert result.total_universe == 528
    assert result.returned_count <= MAX_SCREEN_SYMBOLS


def test_80_eligible_returns_exactly_80():
    rows = [_row(f"S{i:03d}USDT") for i in range(80)]
    sel = ScreenUniverseSelector(ttl_sec=0)
    result = sel.select(rows, total_universe=528, limit=100, force_refresh=True)
    assert result.returned_count == 80
    assert len(result.rows) == 80
    # Do not pad with arbitrary coins
    assert result.returned_count < 100


def test_0_eligible_returns_0():
    rows = [
        _row("BADUSDT", price=None, data_status=DataStatus.UNAVAILABLE),
        _row("STALEUSDT", price_status=DataStatus.STALE),
    ]
    sel = ScreenUniverseSelector(ttl_sec=0)
    result = sel.select(
        rows,
        total_universe=2,
        limit=100,
        force_refresh=True,
        ohlcv_ready_fn=lambda _s: True,
    )
    assert result.returned_count == 0
    assert len(result.rows) == 0


def test_no_hardcoded_symbol_list():
    import inspect
    import app.services.screen_universe as mod

    src = inspect.getsource(mod)
    for banned in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        assert banned not in src


def test_entry_ready_screen_priority():
    ready = _row("READYUSDT", setup="ENTRY_READY", market="BUY")
    waiting = _row("WAITUSDT", setup="WAITING", market="WAITING", vol=9e12)
    assert compute_screen_priority(ready).score > compute_screen_priority(waiting).score
    assert compute_screen_priority(ready).reason == "ENTRY_READY"


def test_long_short_entry_candidate_priority():
    long_c = _row("LONGUSDT", setup="LONG_ENTRY_CANDIDATE", market="BUY")
    short_c = _row("SHORTUSDT", setup="SHORT_ENTRY_CANDIDATE", market="SELL")
    waiting = _row("WAITUSDT", setup="WAITING", market="BUY", vol=9e12)
    assert compute_screen_priority(long_c).reason == "LONG_ENTRY_CANDIDATE"
    assert compute_screen_priority(short_c).reason == "SHORT_ENTRY_CANDIDATE"
    assert compute_screen_priority(long_c).score > compute_screen_priority(waiting).score
    assert compute_screen_priority(short_c).score > compute_screen_priority(waiting).score


def test_waiting_does_not_become_entry_ready():
    buy_no_setup = _row("BNOSUSDT", setup="NO_SETUP", market="BUY")
    scored = compute_screen_priority(buy_no_setup)
    assert scored.reason != "ENTRY_READY"
    assert not matches_screen_filter(buy_no_setup, "ENTRY_READY")


def test_stale_excluded_when_fresh_exist():
    fresh = [_row(f"F{i}USDT") for i in range(50)]
    stale = [_row(f"X{i}USDT", price_status=DataStatus.STALE) for i in range(50)]
    sel = ScreenUniverseSelector(ttl_sec=0)
    result = sel.select(fresh + stale, total_universe=100, limit=100, force_refresh=True)
    assert result.excluded["excluded_stale"] == 50
    assert all(r.price.status != DataStatus.STALE for r in result.rows)
    assert result.returned_count == 50


def test_missing_ohlcv_excluded_when_enough_valid():
    ready = [_row(f"R{i}USDT") for i in range(40)]
    missing = [_row(f"M{i}USDT") for i in range(40)]

    def ohlcv_ready(sym: str) -> bool:
        return sym.startswith("R")

    sel = ScreenUniverseSelector(ttl_sec=0)
    result = sel.select(
        ready + missing,
        total_universe=80,
        limit=100,
        force_refresh=True,
        ohlcv_ready_fn=ohlcv_ready,
    )
    assert result.excluded["excluded_missing_ohlcv"] == 40
    assert result.returned_count == 40
    assert all(r.symbol.startswith("R") for r in result.rows)


def test_search_finds_outside_top_100():
    rows = [_row(f"S{i:03d}USDT", setup="WAITING", market="NEUTRAL", vol=float(i)) for i in range(150)]
    rows.append(_row("XMRUSDT", setup="WAITING", market="WAITING", vol=1.0))
    sel = ScreenUniverseSelector(ttl_sec=0)
    top = sel.select(rows, total_universe=151, limit=100, force_refresh=True)
    top_syms = {r.symbol for r in top.rows}
    # Force XMR out of top by low volume if it somehow got in — check search path
    searched = sel.select(
        rows,
        total_universe=151,
        limit=100,
        search="XMR",
        force_refresh=True,
    )
    assert any(r.symbol == "XMRUSDT" for r in searched.rows)
    assert searched.search_mode is True
    # Search does not permanently rewrite top-100 membership
    top2 = sel.select(rows, total_universe=151, limit=100, force_refresh=False)
    assert "XMRUSDT" not in {r.symbol for r in top2.rows} or "XMRUSDT" in top_syms


def test_search_does_not_permanently_modify_top_100():
    rows = [_row(f"S{i:03d}USDT", vol=1e6 + i) for i in range(120)]
    outsider = _row("OUTUSDT", vol=1.0, setup="WAITING")
    rows.append(outsider)
    sel = ScreenUniverseSelector(ttl_sec=60)
    before = sel.select(rows, total_universe=121, limit=100, force_refresh=True)
    before_syms = [r.symbol for r in before.rows]
    assert "OUTUSDT" not in before_syms
    hit = sel.select(rows, total_universe=121, limit=100, search="OUTUSDT")
    assert hit.rows and hit.rows[0].symbol == "OUTUSDT"
    after = sel.select(rows, total_universe=121, limit=100, force_refresh=False)
    assert [r.symbol for r in after.rows] == before_syms


def test_presets_are_filter_configs_not_coin_lists():
    from app.config import Settings
    from app.services.presets import list_presets

    for p in list_presets(Settings()):
        assert "coins" not in p
        assert "symbols" not in p
        assert isinstance(p["filters"], list)


def test_hard_eligibility_rejects_unavailable():
    bad = _row("BADUSDT", data_status=DataStatus.UNAVAILABLE)
    assert hard_eligibility(bad) is False
    good = _row("GOODUSDT")
    assert hard_eligibility(good, ohlcv_ready=True) is True


def test_returned_count_never_exceeds_100():
    rows = [_row(f"S{i}USDT") for i in range(300)]
    sel = ScreenUniverseSelector(ttl_sec=0)
    result = sel.select(rows, total_universe=300, limit=999, force_refresh=True)
    assert result.returned_count <= 100
    assert result.limit == 100


def test_no_fabricated_priority_reason():
    row = _row("XUSDT", setup="WAITING", market="WAITING")
    scored = compute_screen_priority(row)
    banned = {"likely profitable", "high probability", "best coin", "win_probability"}
    assert scored.reason.lower() not in banned
    assert "profit" not in scored.reason.lower()


def test_screen_filter_entry_ready_excludes_waiting():
    waiting = _row("WUSDT", setup="WAITING")
    long_c = _row("LUSDT", setup="LONG_ENTRY_CANDIDATE")
    assert matches_screen_filter(waiting, "ENTRY_READY") is False
    assert matches_screen_filter(long_c, "ENTRY_READY") is True
