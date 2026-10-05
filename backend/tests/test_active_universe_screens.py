from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.models.schemas import SymbolInfo, TickerUpdate
from app.services.active_universe import list_active_symbols, active_universe_meta
from app.services.market_store import MarketDataStore


def _sym(symbol: str) -> SymbolInfo:
    return SymbolInfo(
        symbol=symbol,
        base_asset=symbol.replace("USDT", ""),
        quote_asset="USDT",
        market_type="futures_perp",
        exchange="binance",
        status="TRADING",
    )


def test_list_active_symbols_caps_and_keeps_paper():
    store = MarketDataStore()
    now = datetime.now(timezone.utc)
    paper = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    junk = [f"JUNK{i}USDT" for i in range(30)]
    for i, sym in enumerate(junk):
        store.tickers[sym] = TickerUpdate(
            symbol=sym,
            market_type="futures_perp",
            price=1.0,
            quote_volume_24h=float(10_000 - i),
            timestamp=now,
            source="test",
        )
    for sym in paper:
        store.tickers[sym] = TickerUpdate(
            symbol=sym,
            market_type="futures_perp",
            price=1.0,
            quote_volume_24h=1.0,
            timestamp=now,
            source="test",
        )
    with patch("app.services.active_universe.market_store", store), patch(
        "app.services.active_universe.get_orchestrator", return_value=None
    ), patch("app.services.active_universe._active_cap", return_value=5):
        active = list_active_symbols(paper + junk)
    assert len(active) == 5
    for sym in paper:
        assert sym in active
    meta = None
    with patch("app.services.active_universe.market_store", store), patch(
        "app.services.active_universe.get_orchestrator", return_value=None
    ), patch("app.services.active_universe._active_cap", return_value=5):
        meta = active_universe_meta(paper + junk)
    assert meta["active_universe"] == 5
    assert meta["discovered_universe"] == 33


def test_screener_scopes_to_active_universe():
    from app.config import Settings
    from app.services.screener import ScreenerService

    store = MarketDataStore()
    now = datetime.now(timezone.utc)
    for i in range(20):
        sym = f"AAA{i}USDT"
        store.symbols[sym] = _sym(sym)
        store.tickers[sym] = TickerUpdate(
            symbol=sym,
            market_type="futures_perp",
            price=1.0,
            quote_volume_24h=float(1000 - i),
            timestamp=now,
            source="test",
        )
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        store.symbols[sym] = _sym(sym)
        store.tickers[sym] = TickerUpdate(
            symbol=sym,
            market_type="futures_perp",
            price=1.0,
            quote_volume_24h=1.0,
            timestamp=now,
            source="test",
        )

    svc = ScreenerService(Settings(USE_REAL_DATA=True), store)
    with patch(
        "app.services.active_universe.list_active_symbols",
        return_value=["BTCUSDT", "ETHUSDT", "SOLUSDT", "AAA0USDT", "AAA1USDT"],
    ):
        rows, total, meta = svc.futures_screener(limit=100, offset=0, apply_screen_universe=False)
    syms = {r.symbol for r in rows}
    assert syms <= {"BTCUSDT", "ETHUSDT", "SOLUSDT", "AAA0USDT", "AAA1USDT"}
    assert meta["active_universe"] == 5
    assert meta["discovered_universe"] == 23
    assert meta["total_universe"] == 5


def test_screener_search_can_find_outside_active():
    from app.config import Settings
    from app.services.screener import ScreenerService

    store = MarketDataStore()
    now = datetime.now(timezone.utc)
    for sym in ("BTCUSDT", "ZZZUSDT"):
        store.symbols[sym] = _sym(sym)
        store.tickers[sym] = TickerUpdate(
            symbol=sym,
            market_type="futures_perp",
            price=1.0,
            quote_volume_24h=100.0,
            timestamp=now,
            source="test",
        )
    svc = ScreenerService(Settings(USE_REAL_DATA=True), store)
    with patch(
        "app.services.active_universe.list_active_symbols",
        return_value=["BTCUSDT"],
    ):
        rows, _, meta = svc.futures_screener(
            search="ZZZ", limit=100, offset=0, apply_screen_universe=False
        )
    assert any(r.symbol == "ZZZUSDT" for r in rows)
    assert meta["search_mode"] is True


def test_screener_large_cap_preset_filters_by_market_cap():
    from app.config import Settings
    from app.models.schemas import DataStatus, FreshValue
    from app.services.engine_store import engine_store
    from app.services.screener import ScreenerService

    store = MarketDataStore()
    now = datetime.now(timezone.utc)
    for sym, mcap in (("BTCUSDT", 1.5e12), ("SMALLUSDT", 2e8)):
        store.symbols[sym] = _sym(sym)
        store.tickers[sym] = TickerUpdate(
            symbol=sym,
            market_type="futures_perp",
            price=1.0,
            quote_volume_24h=1000.0,
            timestamp=now,
            source="test",
        )
        engine_store.set_fundamentals(
            sym,
            {
                "market_cap": FreshValue(
                    value=mcap,
                    timestamp=now,
                    source="test",
                    status=DataStatus.LIVE,
                )
            },
        )
    svc = ScreenerService(Settings(USE_REAL_DATA=True), store)
    with patch(
        "app.services.active_universe.list_active_symbols",
        return_value=["BTCUSDT", "SMALLUSDT"],
    ):
        rows, _, _ = svc.futures_screener(
            preset="large_cap", limit=100, offset=0, apply_screen_universe=False
        )
    assert [r.symbol for r in rows] == ["BTCUSDT"]
