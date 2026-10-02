"""Optional Timescale/Postgres persistence. No-op when DATABASE_ENABLED=false."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import text
from app.core.logging import get_logger
from app.models.ohlcv import Candle
from app.services.database import db_manager
logger = get_logger("persistence")

class PersistenceService:
    """Write market/engine artifacts when DB is enabled; never required for dev."""
    def __init__(self) -> None:
        self._writes = 0
        self._errors = 0
        self._pending_signals: dict[str, dict[str, Any]] = {}

    @property
    def active(self) -> bool:
        return bool(db_manager.enabled and db_manager.engine is not None)

    def queue_signal(
        self,
        *,
        symbol: str,
        timeframe: str,
        state: str,
        reasons: list[Any] | None = None,
        scores: dict[str, Any] | None = None,
    ) -> None:
        """Sync-safe enqueue; no-op when DB disabled. Dedupes by symbol."""
        if not db_manager.enabled:
            return
        self._pending_signals[symbol.upper()] = {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "state": state,
            "reasons": reasons or [],
            "scores": scores or {},
            "ts": datetime.now(timezone.utc),
        }

    async def flush_pending_signals(self) -> int:
        if not self.active or not self._pending_signals:
            self._pending_signals.clear()
            return 0
        pending = list(self._pending_signals.values())
        self._pending_signals.clear()
        n = 0
        for item in pending:
            await self.persist_signal(
                symbol=item["symbol"],
                timeframe=item["timeframe"],
                state=item["state"],
                reasons=item["reasons"],
                scores=item["scores"],
                ts=item["ts"],
            )
            n += 1
        return n

    async def upsert_symbols(self, symbols: list[Any]) -> int:
        if not self.active or not symbols:
            return 0
        sql = text(
            """
            INSERT INTO symbols (
                symbol, base_asset, quote_asset, market_type, exchange, status,
                contract_type, price_precision, qty_precision, updated_at
            ) VALUES (
                :symbol, :base_asset, :quote_asset, :market_type, :exchange, :status,
                :contract_type, :price_precision, :qty_precision, NOW()
            )
            ON CONFLICT (symbol) DO UPDATE SET
                base_asset = EXCLUDED.base_asset,
                quote_asset = EXCLUDED.quote_asset,
                market_type = EXCLUDED.market_type,
                exchange = EXCLUDED.exchange,
                status = EXCLUDED.status,
                contract_type = EXCLUDED.contract_type,
                price_precision = EXCLUDED.price_precision,
                qty_precision = EXCLUDED.qty_precision,
                updated_at = NOW()
            """
        )
        try:
            async with db_manager.engine.begin() as conn:
                for s in symbols:
                    await conn.execute(
                        sql,
                        {
                            "symbol": s.symbol,
                            "base_asset": s.base_asset,
                            "quote_asset": s.quote_asset,
                            "market_type": s.market_type,
                            "exchange": getattr(s, "exchange", "binance") or "binance",
                            "status": getattr(s, "status", "TRADING") or "TRADING",
                            "contract_type": getattr(s, "contract_type", None),
                            "price_precision": getattr(s, "price_precision", None),
                            "qty_precision": getattr(s, "qty_precision", None),
                        },
                    )
            self._writes += len(symbols)
            return len(symbols)
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.warning("persist_symbols_failed", error=str(exc))
            return 0
    async def persist_ohlcv_batch(self, candles: list[Candle]) -> int:
        if not self.active or not candles:
            return 0
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
                open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                close = EXCLUDED.close, volume = EXCLUDED.volume,
                quote_volume = EXCLUDED.quote_volume, trade_count = EXCLUDED.trade_count,
                taker_buy_base = EXCLUDED.taker_buy_base,
                taker_buy_quote = EXCLUDED.taker_buy_quote, source = EXCLUDED.source
            """
        )
        vol_sql = text(
            """
            INSERT INTO volume_history (time, symbol, timeframe, volume, quote_volume, source)
            VALUES (:time, :symbol, :timeframe, :volume, :quote_volume, :source)
            ON CONFLICT (time, symbol, timeframe) DO UPDATE SET
                volume = EXCLUDED.volume, quote_volume = EXCLUDED.quote_volume,
                source = EXCLUDED.source
            """
        )
        try:
            async with db_manager.engine.begin() as conn:
                for c in candles:
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
            self._writes += len(candles)
            return len(candles)
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.warning("persist_ohlcv_failed", error=str(exc))
            return 0
    async def persist_price_snapshot(
        self, symbol: str, price: float, *, source: str = "binance", ts: datetime | None = None
    ) -> None:
        if not self.active:
            return
        sql = text(
            """
            INSERT INTO price_snapshots (time, symbol, price, source)
            VALUES (:time, :symbol, :price, :source)
            ON CONFLICT (time, symbol) DO UPDATE SET price = EXCLUDED.price, source = EXCLUDED.source
            """
        )
        try:
            async with db_manager.engine.begin() as conn:
                await conn.execute(
                    sql,
                    {
                        "time": ts or datetime.now(timezone.utc),
                        "symbol": symbol.upper(),
                        "price": price,
                        "source": source,
                    },
                )
            self._writes += 1
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.warning("persist_price_failed", error=str(exc))
    async def persist_funding(
        self,
        symbol: str,
        funding_rate: float,
        *,
        mark_price: float | None = None,
        source: str = "binance",
        ts: datetime | None = None,
    ) -> None:
        if not self.active:
            return
        sql = text(
            """
            INSERT INTO funding_rates (time, symbol, funding_rate, mark_price, source)
            VALUES (:time, :symbol, :funding_rate, :mark_price, :source)
            ON CONFLICT (time, symbol) DO UPDATE SET
                funding_rate = EXCLUDED.funding_rate,
                mark_price = EXCLUDED.mark_price,
                source = EXCLUDED.source
            """
        )
        try:
            async with db_manager.engine.begin() as conn:
                await conn.execute(
                    sql,
                    {
                        "time": ts or datetime.now(timezone.utc),
                        "symbol": symbol.upper(),
                        "funding_rate": funding_rate,
                        "mark_price": mark_price,
                        "source": source,
                    },
                )
            self._writes += 1
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.warning("persist_funding_failed", error=str(exc))
    async def persist_open_interest(
        self,
        symbol: str,
        open_interest: float,
        *,
        source: str = "binance",
        ts: datetime | None = None,
    ) -> None:
        if not self.active:
            return
        sql = text(
            """
            INSERT INTO open_interest (time, symbol, open_interest, source)
            VALUES (:time, :symbol, :open_interest, :source)
            ON CONFLICT (time, symbol) DO UPDATE SET
                open_interest = EXCLUDED.open_interest, source = EXCLUDED.source
            """
        )
        try:
            async with db_manager.engine.begin() as conn:
                await conn.execute(
                    sql,
                    {
                        "time": ts or datetime.now(timezone.utc),
                        "symbol": symbol.upper(),
                        "open_interest": open_interest,
                        "source": source,
                    },
                )
            self._writes += 1
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.warning("persist_oi_failed", error=str(exc))
    async def persist_liquidation(
        self,
        *,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        quote_qty: float | None = None,
        source: str = "binance",
        ts: datetime | None = None,
    ) -> None:
        if not self.active:
            return
        sql = text(
            """
            INSERT INTO liquidations (time, symbol, side, price, quantity, quote_qty, source)
            VALUES (:time, :symbol, :side, :price, :quantity, :quote_qty, :source)
            ON CONFLICT DO NOTHING
            """
        )
        try:
            async with db_manager.engine.begin() as conn:
                await conn.execute(
                    sql,
                    {
                        "time": ts or datetime.now(timezone.utc),
                        "symbol": symbol.upper(),
                        "side": side,
                        "price": price,
                        "quantity": quantity,
                        "quote_qty": quote_qty,
                        "source": source,
                    },
                )
            self._writes += 1
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.warning("persist_liq_failed", error=str(exc))
    async def persist_signal(
        self,
        *,
        symbol: str,
        timeframe: str,
        state: str,
        reasons: list[Any] | None = None,
        scores: dict[str, Any] | None = None,
        ts: datetime | None = None,
    ) -> None:
        if not self.active:
            return
        sql = text(
            """
            INSERT INTO signals (time, symbol, timeframe, state, reasons, scores)
            VALUES (:time, :symbol, :timeframe, :state, CAST(:reasons AS JSONB), CAST(:scores AS JSONB))
            """
        )
        try:
            async with db_manager.engine.begin() as conn:
                await conn.execute(
                    sql,
                    {
                        "time": ts or datetime.now(timezone.utc),
                        "symbol": symbol.upper(),
                        "timeframe": timeframe,
                        "state": state,
                        "reasons": json.dumps(reasons or []),
                        "scores": json.dumps(scores or {}),
                    },
                )
            self._writes += 1
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.warning("persist_signal_failed", error=str(exc))
    async def persist_structure_events(
        self, symbol: str, timeframe: str, events: list[dict[str, Any]]
    ) -> int:
        if not self.active or not events:
            return 0
        sql = text(
            """
            INSERT INTO structure_events (
                time, symbol, timeframe, event_type, price, strength, evidence, source
            ) VALUES (
                :time, :symbol, :timeframe, :event_type, :price, :strength,
                CAST(:evidence AS JSONB), :source
            )
            ON CONFLICT DO NOTHING
            """
        )
        n = 0
        try:
            async with db_manager.engine.begin() as conn:
                for ev in events:
                    et = str(ev.get("event_type") or ev.get("type") or "")
                    if not et:
                        continue
                    price = ev.get("price")
                    ts = ev.get("timestamp") or ev.get("time") or datetime.now(timezone.utc)
                    if isinstance(ts, str):
                        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    await conn.execute(
                        sql,
                        {
                            "time": ts,
                            "symbol": symbol.upper(),
                            "timeframe": timeframe,
                            "event_type": et,
                            "price": float(price) if price is not None else 0.0,
                            "strength": ev.get("strength"),
                            "evidence": json.dumps(ev.get("evidence") or {}),
                            "source": "market_structure",
                        },
                    )
                    n += 1
            self._writes += n
            return n
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.warning("persist_structure_failed", error=str(exc))
            return 0
    async def persist_supply_demand_zones(
        self, symbol: str, zones: list[dict[str, Any]]
    ) -> int:
        if not self.active or not zones:
            return 0
        # Replace active zones for symbol (simple upsert by delete+insert of current set)
        delete_sql = text("DELETE FROM supply_demand_zones WHERE symbol = :symbol")
        insert_sql = text(
            """
            INSERT INTO supply_demand_zones (
                symbol, timeframe, zone_type, high, low, created_at, strength,
                freshness, touch_count, reaction_strength, status, updated_at
            ) VALUES (
                :symbol, :timeframe, :zone_type, :high, :low, :created_at, :strength,
                :freshness, :touch_count, :reaction_strength, :status, NOW()
            )
            """
        )
        try:
            async with db_manager.engine.begin() as conn:
                await conn.execute(delete_sql, {"symbol": symbol.upper()})
                for z in zones:
                    created = z.get("created_at") or datetime.now(timezone.utc)
                    if isinstance(created, str):
                        created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    await conn.execute(
                        insert_sql,
                        {
                            "symbol": symbol.upper(),
                            "timeframe": z.get("timeframe") or "15m",
                            "zone_type": z.get("zone_type") or "UNKNOWN",
                            "high": float(z.get("high") or 0),
                            "low": float(z.get("low") or 0),
                            "created_at": created,
                            "strength": z.get("strength"),
                            "freshness": z.get("freshness"),
                            "touch_count": int(z.get("touch_count") or 0),
                            "reaction_strength": z.get("reaction_strength"),
                            "status": z.get("status") or "ACTIVE",
                        },
                    )
            self._writes += len(zones)
            return len(zones)
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.warning("persist_zones_failed", error=str(exc))
            return 0
    async def load_closes_near(
        self, symbol: str, *, timeframe: str = "1d", before: datetime, limit: int = 1
    ) -> list[tuple[datetime, float]]:
        """Load closes at/before timestamp from DB when memory history is short."""
        if not self.active:
            return []
        sql = text(
            """
            SELECT time, close FROM ohlcv
            WHERE symbol = :symbol AND timeframe = :tf AND time <= :before
            ORDER BY time DESC LIMIT :lim
            """
        )
        try:
            async with db_manager.engine.begin() as conn:
                result = await conn.execute(
                    sql,
                    {
                        "symbol": symbol.upper(),
                        "tf": timeframe,
                        "before": before,
                        "lim": limit,
                    },
                )
                rows = result.fetchall()
            return [(r[0], float(r[1])) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("load_closes_failed", error=str(exc))
            return []
    async def load_latest_setup_analyses(
        self, *, limit_per_symbol: int = 1, max_symbols: int = 2000
    ) -> dict[str, dict[str, Any]]:
        """Hydrate in-memory setup cache from DB after restart."""
        if not self.active:
            return {}
        sql = text(
            """
            SELECT DISTINCT ON (symbol)
                symbol, timeframe, status, direction, payload, time
            FROM setup_analyses
            ORDER BY symbol, time DESC
            LIMIT :lim
            """
        )
        try:
            async with db_manager.engine.begin() as conn:
                result = await conn.execute(sql, {"lim": max_symbols})
                rows = result.fetchall()
            out: dict[str, dict[str, Any]] = {}
            for r in rows:
                payload = r[4]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                if not isinstance(payload, dict):
                    payload = {}
                payload.setdefault("symbol", r[0])
                payload.setdefault("timeframe", r[1])
                payload.setdefault("status", r[2])
                payload.setdefault("direction", r[3])
                # Restart hydrate: mark until verified against current candles
                payload["signal_status"] = payload.get("signal_status") or "LIVE"
                payload["hydrated_from_db"] = True
                out[str(r[0]).upper()] = payload
            return out
        except Exception as exc:  # noqa: BLE001
            logger.warning("load_setup_analyses_failed", error=str(exc))
            return {}

    async def persist_setup_analysis(self, symbol: str, payload: dict[str, Any]) -> None:
        """Persist full setup snapshot + atomic setup_events (no duplicates of OHLCV)."""
        if not self.active or not payload:
            return
        tf = str(payload.get("timeframe") or "15m")
        status = str(payload.get("status") or "NO_SETUP")
        direction = payload.get("direction")
        sql = text(
            """
            INSERT INTO setup_analyses (
                time, symbol, timeframe, status, direction, payload, created_at
            ) VALUES (
                NOW(), :symbol, :timeframe, :status, :direction, CAST(:payload AS JSONB), NOW()
            )
            """
        )
        try:
            async with db_manager.engine.begin() as conn:
                await conn.execute(
                    sql,
                    {
                        "symbol": symbol.upper(),
                        "timeframe": tf,
                        "status": status,
                        "direction": direction,
                        "payload": json.dumps(payload, default=str),
                    },
                )
            self._writes += 1
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.warning("persist_setup_analysis_failed", error=str(exc))
            return

        events: list[dict[str, Any]] = []
        bos = payload.get("bos") or {}
        if bos.get("state") == "CONFIRMED" and bos.get("direction"):
            events.append(
                {
                    "event_type": str(bos["direction"]),
                    "price": bos.get("broken_level") or bos.get("break_price") or 0,
                    "state": bos.get("state"),
                    "time": bos.get("break_timestamp"),
                    "evidence": {"reason": bos.get("reason")},
                }
            )
        choch = payload.get("choch") or {}
        if choch.get("state") == "CONFIRMED" and choch.get("direction"):
            events.append(
                {
                    "event_type": str(choch["direction"]),
                    "price": choch.get("level") or choch.get("break_price") or 0,
                    "state": choch.get("state"),
                    "time": choch.get("break_timestamp"),
                    "evidence": {"reason": choch.get("reason")},
                }
            )
        impulse = payload.get("impulse") or {}
        if impulse.get("is_impulse"):
            events.append(
                {
                    "event_type": f"IMPULSE_{impulse.get('direction')}",
                    "price": impulse.get("impulse_end") or 0,
                    "state": impulse.get("quality"),
                    "evidence": {
                        "atr_multiple": impulse.get("atr_multiple"),
                        "rvol": impulse.get("rvol"),
                    },
                }
            )
        if events:
            await self.persist_setup_events(symbol, tf, events)

    async def persist_setup_events(
        self, symbol: str, timeframe: str, events: list[dict[str, Any]]
    ) -> int:
        if not self.active or not events:
            return 0
        sql = text(
            """
            INSERT INTO setup_events (
                time, symbol, timeframe, event_type, price, state, evidence, source
            ) VALUES (
                :time, :symbol, :timeframe, :event_type, :price, :state,
                CAST(:evidence AS JSONB), :source
            )
            ON CONFLICT DO NOTHING
            """
        )
        n = 0
        try:
            async with db_manager.engine.begin() as conn:
                for ev in events:
                    et = str(ev.get("event_type") or "")
                    if not et:
                        continue
                    ts = ev.get("time") or datetime.now(timezone.utc)
                    if isinstance(ts, str):
                        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    await conn.execute(
                        sql,
                        {
                            "time": ts,
                            "symbol": symbol.upper(),
                            "timeframe": timeframe,
                            "event_type": et,
                            "price": float(ev.get("price") or 0),
                            "state": ev.get("state"),
                            "evidence": json.dumps(ev.get("evidence") or {}, default=str),
                            "source": "setup_signal_engine",
                        },
                    )
                    n += 1
            self._writes += n
            return n
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.warning("persist_setup_events_failed", error=str(exc))
            return 0

    async def persist_paper_trade(self, trade: dict[str, Any]) -> None:
        """Upsert one paper trade row (open or closed)."""
        if not self.active or not trade:
            return
        sql = text(
            """
            INSERT INTO paper_trades (
                id, symbol, side, status, entry_price, stop_price, tp1_price,
                quantity, risk_usd, opened_at, closed_at, exit_price, exit_reason,
                pnl_usd, r_multiple, source_candle_ts, timeframe, signal_snippet, updated_at
            ) VALUES (
                :id, :symbol, :side, :status, :entry_price, :stop_price, :tp1_price,
                :quantity, :risk_usd, CAST(:opened_at AS TIMESTAMPTZ),
                CAST(:closed_at AS TIMESTAMPTZ), :exit_price, :exit_reason,
                :pnl_usd, :r_multiple, :source_candle_ts, :timeframe,
                CAST(:signal_snippet AS JSONB), NOW()
            )
            ON CONFLICT (id) DO UPDATE SET
                status = EXCLUDED.status,
                closed_at = EXCLUDED.closed_at,
                exit_price = EXCLUDED.exit_price,
                exit_reason = EXCLUDED.exit_reason,
                pnl_usd = EXCLUDED.pnl_usd,
                r_multiple = EXCLUDED.r_multiple,
                signal_snippet = EXCLUDED.signal_snippet,
                updated_at = NOW()
            """
        )
        try:
            async with db_manager.engine.begin() as conn:
                await conn.execute(
                    sql,
                    {
                        "id": str(trade.get("id")),
                        "symbol": str(trade.get("symbol") or "").upper(),
                        "side": str(trade.get("side") or "LONG"),
                        "status": str(trade.get("status") or "OPEN"),
                        "entry_price": float(trade.get("entry_price") or 0),
                        "stop_price": float(trade.get("stop_price") or 0),
                        "tp1_price": trade.get("tp1_price"),
                        "quantity": float(trade.get("quantity") or 0),
                        "risk_usd": trade.get("risk_usd"),
                        "opened_at": trade.get("opened_at")
                        or datetime.now(timezone.utc).isoformat(),
                        "closed_at": trade.get("closed_at"),
                        "exit_price": trade.get("exit_price"),
                        "exit_reason": trade.get("exit_reason"),
                        "pnl_usd": trade.get("pnl_usd"),
                        "r_multiple": trade.get("r_multiple"),
                        "source_candle_ts": trade.get("source_candle_ts"),
                        "timeframe": trade.get("timeframe"),
                        "signal_snippet": json.dumps(
                            trade.get("signal_snippet") or {}, default=str
                        ),
                    },
                )
            self._writes += 1
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.warning("persist_paper_trade_failed", error=str(exc))

    def stats(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "writes": self._writes,
            "errors": self._errors,
            "database": db_manager.status,
        }


persistence = PersistenceService()
