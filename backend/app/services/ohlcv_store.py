from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.core.logging import get_logger
from app.ingestion.klines import detect_gaps, normalize_timeframe
from app.models.ohlcv import Candle, GapInfo
from app.models.schemas import DataStatus
from app.services.database import db_manager

logger = get_logger("ohlcv_store")


class OHLCVStore:
    """Closed candles in memory (+ optional Timescale); open candles in memory/Redis."""

    def __init__(self, *, history_limit: int = 500) -> None:
        self.history_limit = history_limit
        # key: (symbol, timeframe) -> deque of closed candles
        self._closed: dict[tuple[str, str], deque[Candle]] = defaultdict(
            lambda: deque(maxlen=history_limit)
        )
        self._open: dict[tuple[str, str], Candle] = {}
        self._live_symbols: set[str] = set()
        self._closed_count = 0
        self._open_updates = 0
        self._db_writes = 0
        self._pending_db: list[Candle] = []
        self.redis = None

    async def connect_redis(self, redis_client) -> None:
        self.redis = redis_client

    def live_kline_symbols(self) -> int:
        return len(self._live_symbols)

    def closed_candle_count(self) -> int:
        return self._closed_count

    def get_open(self, symbol: str, timeframe: str) -> Candle | None:
        return self._open.get((symbol.upper(), normalize_timeframe(timeframe)))

    def get_closed(
        self, symbol: str, timeframe: str, *, limit: int | None = None
    ) -> list[Candle]:
        key = (symbol.upper(), normalize_timeframe(timeframe))
        candles = list(self._closed.get(key, ()))
        if limit is not None:
            return candles[-limit:]
        return candles

    def get_candles_for_engine(
        self, symbol: str, timeframe: str, *, include_open: bool = False
    ) -> list[dict[str, Any]]:
        closed = [c.as_mapping() for c in self.get_closed(symbol, timeframe)]
        if include_open:
            op = self.get_open(symbol, timeframe)
            if op is not None:
                closed.append(op.as_mapping())
        return closed

    async def upsert_candle(self, candle: Candle) -> tuple[bool, Candle]:
        """
        Update open candle or append closed candle.
        Returns (closed_event, candle).
        """
        key = (candle.symbol.upper(), normalize_timeframe(candle.timeframe))
        self._live_symbols.add(candle.symbol.upper())

        if candle.is_closed:
            hist = self._closed[key]
            if hist and hist[-1].open_time == candle.open_time:
                hist[-1] = candle
            else:
                hist.append(candle)
            self._closed_count += 1
            self._open.pop(key, None)
            self._pending_db.append(candle)
            if self.redis is not None:
                try:
                    await self.redis.delete(f"ohlcv:open:{key[0]}:{key[1]}")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("redis_open_delete_failed", error=str(exc))
            return True, candle

        self._open[key] = candle
        self._open_updates += 1
        if self.redis is not None:
            try:
                await self.redis.set(
                    f"ohlcv:open:{key[0]}:{key[1]}",
                    candle.model_dump_json(),
                    ex=3600,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("redis_open_set_failed", error=str(exc))
        return False, candle

    async def apply_live_price(self, symbol: str, price: float) -> int:
        """Nudge all open candles for a symbol toward the live trade/mark price."""
        if price is None or price <= 0:
            return 0
        sym = symbol.upper()
        n = 0
        for (s, tf), candle in list(self._open.items()):
            if s != sym or candle.is_closed:
                continue
            hi = max(float(candle.high), float(price))
            lo = min(float(candle.low), float(price))
            if hi == candle.high and lo == candle.low and float(candle.close) == float(price):
                continue
            updated = candle.model_copy(
                update={
                    "high": hi,
                    "low": lo,
                    "close": float(price),
                    "timestamp": datetime.now(timezone.utc),
                    "source": f"{candle.source}+live",
                    "status": DataStatus.LIVE,
                }
            )
            self._open[(s, tf)] = updated
            self._open_updates += 1
            n += 1
        return n

    async def ingest_history(self, candles: list[Candle]) -> int:
        """Load closed REST history without duplicate open_times."""
        n = 0
        for candle in sorted(candles, key=lambda c: c.open_time):
            if not candle.is_closed:
                continue
            self._live_symbols.add(candle.symbol.upper())
            key = (candle.symbol.upper(), normalize_timeframe(candle.timeframe))
            hist = self._closed[key]
            if hist and any(c.open_time == candle.open_time for c in hist):
                # replace matching — still upsert to Postgres so tip repairs persist
                for i, existing in enumerate(hist):
                    if existing.open_time == candle.open_time:
                        hist[i] = candle
                        self._pending_db.append(candle)
                        n += 1
                        break
            else:
                hist.append(candle)
                self._closed_count += 1
                self._pending_db.append(candle)
                n += 1
        return n

    def find_gaps(self, symbol: str, timeframe: str) -> list[GapInfo]:
        return detect_gaps(self.get_closed(symbol, timeframe), timeframe)

    async def load_from_db(
        self,
        *,
        symbols: list[str] | None = None,
        timeframes: list[str] | None = None,
        limit_per_series: int = 500,
    ) -> int:
        """Hydrate closed candles from Timescale/Postgres when DATABASE_ENABLED."""
        if not db_manager.enabled or db_manager.engine is None:
            return 0
        tfs = [normalize_timeframe(t) for t in (timeframes or ["1d", "15m", "1h", "4h", "5m", "1m"])]
        loaded = 0
        try:
            async with db_manager.engine.begin() as conn:
                if symbols:
                    for sym in symbols:
                        for tf in tfs:
                            loaded += await self._load_series(
                                conn, sym.upper(), tf, limit_per_series
                            )
                else:
                    # Distinct symbol/tf pairs present in DB
                    result = await conn.execute(
                        text(
                            "SELECT DISTINCT symbol, timeframe FROM ohlcv "
                            "WHERE timeframe = ANY(:tfs)"
                        ),
                        {"tfs": tfs},
                    )
                    pairs = result.fetchall()
                    for sym, tf in pairs:
                        loaded += await self._load_series(
                            conn, str(sym).upper(), normalize_timeframe(str(tf)), limit_per_series
                        )
            logger.info("ohlcv_hydrated_from_db", candles=loaded)
            return loaded
        except Exception as exc:  # noqa: BLE001
            logger.warning("ohlcv_hydrate_failed", error=str(exc))
            return 0

    async def _load_series(self, conn, symbol: str, timeframe: str, limit: int) -> int:
        result = await conn.execute(
            text(
                """
                SELECT time, symbol, timeframe, open, high, low, close, volume,
                       quote_volume, trade_count, taker_buy_base, taker_buy_quote, source
                FROM ohlcv
                WHERE symbol = :symbol AND timeframe = :tf
                ORDER BY time DESC
                LIMIT :lim
                """
            ),
            {"symbol": symbol, "tf": timeframe, "lim": limit},
        )
        rows = list(reversed(result.fetchall()))
        if not rows:
            return 0
        candles: list[Candle] = []
        for r in rows:
            ot = r[0]
            if ot.tzinfo is None:
                ot = ot.replace(tzinfo=timezone.utc)
            candles.append(
                Candle(
                    symbol=str(r[1]),
                    timeframe=normalize_timeframe(str(r[2])),
                    open_time=ot,
                    close_time=ot,
                    open=float(r[3]),
                    high=float(r[4]),
                    low=float(r[5]),
                    close=float(r[6]),
                    volume=float(r[7]),
                    quote_volume=float(r[8]) if r[8] is not None else None,
                    trade_count=int(r[9]) if r[9] is not None else None,
                    taker_buy_volume=float(r[10]) if r[10] is not None else None,
                    taker_buy_quote_volume=float(r[11]) if r[11] is not None else None,
                    is_closed=True,
                    timestamp=ot,
                    source=str(r[12] or "db"),
                    status=DataStatus.CACHED,
                )
            )
        # Ingest without re-queueing DB writes for already-persisted rows
        n = 0
        for candle in candles:
            key = (candle.symbol.upper(), normalize_timeframe(candle.timeframe))
            self._live_symbols.add(key[0])
            hist = self._closed[key]
            if hist and any(c.open_time == candle.open_time for c in hist):
                continue
            hist.append(candle)
            self._closed_count += 1
            n += 1
        return n

    async def flush_db(self) -> int:
        if not self._pending_db or not db_manager.enabled or db_manager.engine is None:
            self._pending_db.clear()
            return 0
        batch = self._pending_db[:500]
        self._pending_db = self._pending_db[500:]
        sql = text(
            """
            INSERT INTO ohlcv (
                time, symbol, timeframe, open, high, low, close, volume,
                quote_volume, trade_count, taker_buy_base, taker_buy_quote, source
            ) VALUES (
                :time, :symbol, :timeframe, :open, :high, :low, :close, :volume,
                :quote_volume, :trade_count, :taker_buy_base, :taker_buy_quote, :source
            )
            ON CONFLICT (time, symbol, timeframe) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                quote_volume = EXCLUDED.quote_volume,
                trade_count = EXCLUDED.trade_count,
                taker_buy_base = EXCLUDED.taker_buy_base,
                taker_buy_quote = EXCLUDED.taker_buy_quote,
                source = EXCLUDED.source
            """
        )
        vol_sql = text(
            """
            INSERT INTO volume_history (time, symbol, timeframe, volume, quote_volume, source)
            VALUES (:time, :symbol, :timeframe, :volume, :quote_volume, :source)
            ON CONFLICT (time, symbol, timeframe) DO UPDATE SET
                volume = EXCLUDED.volume,
                quote_volume = EXCLUDED.quote_volume,
                source = EXCLUDED.source
            """
        )
        price_sql = text(
            """
            INSERT INTO price_snapshots (time, symbol, price, source)
            VALUES (:time, :symbol, :price, :source)
            ON CONFLICT (time, symbol) DO UPDATE SET
                price = EXCLUDED.price, source = EXCLUDED.source
            """
        )
        try:
            async with db_manager.engine.begin() as conn:
                for c in batch:
                    params = {
                        "time": c.open_time,
                        "symbol": c.symbol,
                        "timeframe": c.timeframe,
                        "open": c.open,
                        "high": c.high,
                        "low": c.low,
                        "close": c.close,
                        "volume": c.volume,
                        "quote_volume": c.quote_volume,
                        "trade_count": c.trade_count,
                        "taker_buy_base": c.taker_buy_volume,
                        "taker_buy_quote": c.taker_buy_quote_volume,
                        "source": c.source,
                    }
                    await conn.execute(sql, params)
                    await conn.execute(
                        vol_sql,
                        {
                            "time": c.open_time,
                            "symbol": c.symbol,
                            "timeframe": c.timeframe,
                            "volume": c.volume,
                            "quote_volume": c.quote_volume,
                            "source": c.source,
                        },
                    )
                    if c.timeframe in ("1m", "1h", "1d"):
                        await conn.execute(
                            price_sql,
                            {
                                "time": c.open_time,
                                "symbol": c.symbol,
                                "price": c.close,
                                "source": c.source,
                            },
                        )
            self._db_writes += len(batch)
            return len(batch)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ohlcv_db_flush_failed", error=str(exc))
            # Keep trying later
            self._pending_db = batch + self._pending_db
            return 0

    def remove_symbol(self, symbol: str) -> None:
        sym = symbol.upper()
        self._live_symbols.discard(sym)
        for key in list(self._closed.keys()):
            if key[0] == sym:
                del self._closed[key]
        for key in list(self._open.keys()):
            if key[0] == sym:
                del self._open[key]

    def stats(self) -> dict[str, Any]:
        return {
            "kline_live_symbols": len(self._live_symbols),
            "closed_candles_ingested": self._closed_count,
            "open_candle_updates": self._open_updates,
            "open_candles": len(self._open),
            "series_count": len(self._closed),
            "db_writes": self._db_writes,
            "pending_db": len(self._pending_db),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


ohlcv_store = OHLCVStore()
