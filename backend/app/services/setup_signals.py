"""Service layer: run SignalEngine on real OHLCV + engine_store context.

Recomputes on candle-close / backfill / visible ensure — not every ticker tick.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from app.config import Settings, get_settings
from app.models.schemas import DataStatus, FreshValue
from app.signals.config import SignalConfig
from app.signals.signal_engine import SignalEngine
from app.services.engine_store import engine_store
from app.services.ohlcv_store import ohlcv_store


class SetupSignalService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        raw = dict(self.settings.indicators_config or {})
        self.config = SignalConfig.from_mapping(raw)
        # Env research gates override YAML (still default False)
        self.config.research_gate_enabled = bool(self.settings.research_gate_enabled)
        self.config.research_gate_block_shorts = bool(
            self.settings.research_gate_block_shorts
        )
        self.config.research_gate_block_htf_conflict = bool(
            self.settings.research_gate_block_htf_conflict
        )
        self.engine = SignalEngine(self.config)
        # Performance counters (process-local)
        self.calc_latencies_ms: list[float] = []
        self.symbols_recomputed = 0
        self.timeframes_touched = 0
        self.signals_updated = 0
        self.queue_depth = 0
        self._pending_ensure: set[str] = set()

    def _zone_tuple(self, label: str | None) -> tuple[float, float] | None:
        if not label or ":" not in label:
            return None
        try:
            parts = label.split(":")
            mid = float(parts[1])
            return (mid * 0.995, mid * 1.005)
        except (IndexError, ValueError):
            return None

    def current_source_timestamps(self, symbol: str) -> dict[str, str | None]:
        """Latest closed candle timestamps for MTF roles (real OHLCV only)."""
        sym = symbol.upper()
        cfg = self.config
        out: dict[str, str | None] = {}
        for tf in (cfg.mtf_major, cfg.mtf_primary, cfg.mtf_setup, cfg.mtf_entry):
            candles = ohlcv_store.get_closed(sym, tf, limit=1)
            if not candles:
                out[tf] = None
                continue
            ts = candles[-1].open_time
            if isinstance(ts, datetime):
                out[tf] = (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).isoformat()
            else:
                out[tf] = str(ts)
        return out

    def setup_ohlcv_ready(self, symbol: str, *, min_bars: int = 5) -> bool:
        """True when setup timeframe has enough closed candles to compute."""
        candles = ohlcv_store.get_closed(symbol.upper(), self.config.mtf_setup)
        return len(candles) >= min_bars

    def is_stale(self, payload: dict[str, Any] | None, symbol: str) -> bool:
        """Stale if setup TF source candle advanced beyond what the payload used."""
        if not payload:
            return True
        if payload.get("signal_status") == "WAITING" and not self.setup_ohlcv_ready(symbol):
            return False  # still legitimately waiting
        cached_ts = (payload.get("source_candle_timestamps") or {}).get(self.config.mtf_setup)
        current = self.current_source_timestamps(symbol).get(self.config.mtf_setup)
        if current is None:
            return False
        if cached_ts is None and self.setup_ohlcv_ready(symbol):
            return True
        return cached_ts != current

    def analyze_symbol(
        self,
        symbol: str,
        *,
        account_equity: float | None = None,
        risk_percent: float | None = None,
        leverage: float | None = None,
        triggered_timeframe: str | None = None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        sym = symbol.upper()
        cfg = self.config
        candles_by_tf: dict[str, list] = {}
        rvol_by_tf: dict[str, float | None] = {}
        tfs_used = 0
        for tf in (cfg.mtf_major, cfg.mtf_primary, cfg.mtf_setup, cfg.mtf_entry, cfg.mtf_refine):
            candles = ohlcv_store.get_candles_for_engine(sym, tf, include_open=False)
            candles_by_tf[tf] = candles
            if candles:
                tfs_used += 1
            rvol_fv = engine_store.get_rvol(sym, tf)
            rvol_by_tf[tf] = float(rvol_fv.value) if rvol_fv.value is not None else None

        price = None
        try:
            from app.services.market_store import market_store

            ticker = market_store.get_ticker(sym)
            if ticker:
                price = ticker.price
        except Exception:  # noqa: BLE001
            price = None

        supply_z, demand_z = engine_store.nearest_zones(sym, price)
        demand = self._zone_tuple(demand_z.value if demand_z.status == DataStatus.LIVE else None)
        supply = self._zone_tuple(supply_z.value if supply_z.status == DataStatus.LIVE else None)

        inds = (engine_store.indicators.get(sym) or {}).get(cfg.mtf_setup) or {}
        vwap = inds.get("vwap")
        ema = inds.get("ema_20") or inds.get("ema20")
        vwap_v = float(vwap.value) if isinstance(vwap, FreshValue) and vwap.value is not None else None
        ema_v = float(ema.value) if isinstance(ema, FreshValue) and ema.value is not None else None

        oi_status = "N/A"
        liq_status = "N/A"
        from app.engines.orchestrator import get_orchestrator

        orch = get_orchestrator()
        if orch is not None:
            st = orch.oi.get_state(sym)
            if st is not None and st.open_interest.status == DataStatus.LIVE:
                oi_status = "LIVE"
            elif st is not None:
                oi_status = st.open_interest.status.value
            else:
                oi_status = "WAITING"
            liq_status = orch.liquidations.liquidation_status().value

        analysis = self.engine.analyze(
            sym,
            candles_by_tf,
            rvol_by_tf=rvol_by_tf,
            demand_zone=demand,
            supply_zone=supply,
            vwap=vwap_v,
            ema=ema_v,
            oi_status=oi_status,
            liquidation_status=liq_status,
            account_equity=account_equity,
            risk_percent=risk_percent,
            leverage=leverage,
        )
        payload = analysis.to_dict()
        # Mark STALE only via is_stale() after a newer candle arrives without recompute
        if payload.get("signal_status") != "WAITING":
            payload["signal_status"] = "LIVE"
        # Surface wall-clock tip lag separately — do not conflate with
        # signal_status=STALE (which means source candle advanced, recompute pending).
        from app.ingestion.klines import is_trailing_stale

        setup_closed = ohlcv_store.get_closed(sym, cfg.mtf_setup)
        if not setup_closed:
            payload["ohlcv_freshness"] = "WAITING"
        elif is_trailing_stale(setup_closed, cfg.mtf_setup):
            payload["ohlcv_freshness"] = "TRAILING_STALE"
        else:
            payload["ohlcv_freshness"] = "OK"
        payload["triggered_timeframe"] = triggered_timeframe
        engine_store.set_setup_signal(sym, payload)

        ms = (time.perf_counter() - t0) * 1000.0
        self.calc_latencies_ms.append(ms)
        if len(self.calc_latencies_ms) > 500:
            self.calc_latencies_ms = self.calc_latencies_ms[-500:]
        self.symbols_recomputed += 1
        self.timeframes_touched += max(tfs_used, 1)
        self.signals_updated += 1
        self._pending_ensure.discard(sym)
        self.queue_depth = len(self._pending_ensure)

        try:
            from app.services.performance import performance_monitor

            performance_monitor.record_signal_calc(ms)
        except Exception:  # noqa: BLE001
            pass

        return payload

    def ensure_computed(
        self,
        symbol: str,
        *,
        triggered_timeframe: str | None = None,
        force: bool = False,
    ) -> dict[str, Any] | None:
        """
        Compute if OHLCV ready and cache missing/stale.
        Does nothing when setup TF still unavailable (keeps WAITING honestly).
        """
        sym = symbol.upper()
        cached = engine_store.get_setup_signal(sym)
        if not force and cached and not self.is_stale(cached, sym):
            return cached
        if not self.setup_ohlcv_ready(sym):
            # Honest WAITING placeholder so UI can show dependency, not blank forever
            if not cached:
                from app.signals.market_signal_engine import classify_market_signal

                waiting = {
                    "symbol": sym,
                    "status": "WAITING",
                    "signal_status": "WAITING",
                    "timeframe": self.config.mtf_setup,
                    "trend": {},
                    "data_dependencies": {
                        self.config.mtf_setup: "WAITING FOR OHLCV",
                    },
                    "explanation": [
                        f"{self.config.mtf_setup.upper()}: WAITING FOR OHLCV — no setup generated"
                    ],
                    "source_candle_timestamps": self.current_source_timestamps(sym),
                    "calculated_at": datetime.now(timezone.utc).isoformat(),
                    "disclaimer": (
                        "Structural setup identification only. Not a profitability prediction."
                    ),
                }
                ms = classify_market_signal(analysis=waiting, config=self.config)
                waiting["market_signal"] = ms.get("market_signal")
                waiting["market_signal_reason"] = ms.get("market_signal_reason")
                waiting["confirmation_strength"] = ms.get("confirmation_strength")
                waiting["confirmation_denominator"] = ms.get("confirmation_denominator")
                waiting["market_signal_payload"] = ms
                waiting["classification_version"] = ms.get("classification_version")
                engine_store.set_setup_signal(sym, waiting)
                return waiting
            return cached
        return self.analyze_symbol(sym, triggered_timeframe=triggered_timeframe)

    def enqueue_ensure(self, symbols: list[str]) -> int:
        """Queue symbols for dependency-aware recompute (visible / dirty)."""
        n = 0
        for s in symbols:
            sym = s.upper()
            cached = engine_store.get_setup_signal(sym)
            if cached and not self.is_stale(cached, sym):
                continue
            if sym not in self._pending_ensure:
                self._pending_ensure.add(sym)
                n += 1
        self.queue_depth = len(self._pending_ensure)
        return n

    def drain_ensure_queue(self, *, limit: int = 20) -> list[str]:
        """Process up to `limit` pending symbols. Returns symbols updated."""
        updated: list[str] = []
        batch = list(self._pending_ensure)[:limit]
        for sym in batch:
            before = engine_store.get_setup_signal(sym)
            payload = self.ensure_computed(sym)
            if payload is not None and payload is not before:
                updated.append(sym)
            elif payload and (not before or self.is_stale(before, sym) is False):
                # ensure_computed may have refreshed
                if before is None or before.get("calculated_at") != payload.get("calculated_at"):
                    updated.append(sym)
        self.queue_depth = len(self._pending_ensure)
        return updated

    def mark_stale_if_source_advanced(self, symbol: str) -> bool:
        """If cache exists but source candle moved, flip signal_status to STALE."""
        sym = symbol.upper()
        cached = engine_store.get_setup_signal(sym)
        if not cached or not self.is_stale(cached, sym):
            return False
        if cached.get("signal_status") == "WAITING":
            return False
        cached = dict(cached)
        cached["signal_status"] = "STALE"
        engine_store.set_setup_signal(sym, cached)
        return True

    def screener_fields(self, symbol: str, *, compute_if_missing: bool = False) -> dict[str, FreshValue]:
        """FreshValue columns for Futures table.

        Screener list reads cache. Missing/stale visible symbols should be
        ensured via orchestrator queue — optional compute_if_missing for API/detail.
        """
        cached = engine_store.get_setup_signal(symbol)
        if compute_if_missing and (not cached or self.is_stale(cached, symbol)):
            try:
                cached = self.ensure_computed(symbol)
            except Exception:  # noqa: BLE001
                cached = engine_store.get_setup_signal(symbol)
        src = self.engine.SOURCE
        if not cached:
            w = FreshValue.waiting(src, methodology="WAITING FOR OHLCV / setup engine")
            return {
                "setup_trend": w,
                "setup_bos": w,
                "setup_impulse": w,
                "setup_pullback": w,
                "setup_entry": w,
                "setup_sl": w,
                "setup_tp1": w,
                "setup_rr": w,
                "setup_signal": w,
                "market_signal": w,
                "confirmation_strength": w,
            }

        # Present STALE as STALE status on FreshValues (value retained)
        is_stale = cached.get("signal_status") == "STALE" or self.is_stale(cached, symbol)
        if is_stale and cached.get("signal_status") != "STALE":
            self.mark_stale_if_source_advanced(symbol)
            cached = engine_store.get_setup_signal(symbol) or cached

        trend = cached.get("trend") or {}
        setup_tf = self.config.mtf_setup
        trend_label = trend.get(setup_tf) or trend.get("15m")
        bos = cached.get("bos") or {}
        impulse = cached.get("impulse") or {}
        pullback = cached.get("pullback") or {}
        entry = cached.get("entry") or {}
        stop = cached.get("stop") or {}
        targets = cached.get("targets") or []
        rr = cached.get("risk_reward") or {}
        status = cached.get("status")
        calc_at = cached.get("calculated_at")
        calc_dt = None
        if isinstance(calc_at, str):
            try:
                calc_dt = datetime.fromisoformat(calc_at.replace("Z", "+00:00"))
            except ValueError:
                calc_dt = None

        def wrap(v: Any, methodology: str | None = None) -> FreshValue:
            if v is None:
                return FreshValue.waiting(src, methodology=methodology)
            st = DataStatus.STALE if is_stale else DataStatus.LIVE
            return FreshValue(
                value=v,
                timestamp=calc_dt or datetime.now(timezone.utc),
                source=src,
                status=st,
                methodology=methodology,
            )

        tp1 = targets[0]["target_price"] if targets else None
        best_r = rr.get("best_R")
        bos_label = bos.get("direction") if bos.get("state") == "CONFIRMED" else None
        impulse_label = (
            impulse.get("quality")
            if impulse.get("is_impulse")
            else ("WAITING" if status == "WAITING" else "NONE")
        )
        pb_label = pullback.get("pullback_state")

        ms = cached.get("market_signal")
        conf = cached.get("confirmation_strength")
        denom = cached.get("confirmation_denominator") or 10
        conf_label = f"{conf}/{denom}" if conf is not None else None

        if status == "WAITING" and (not ms or ms == "WAITING"):
            waiting = FreshValue.waiting(src, methodology="WAITING FOR OHLCV")
            return {
                "setup_trend": wrap(trend_label) if trend_label and trend_label != "WAITING" else waiting,
                "setup_bos": waiting,
                "setup_impulse": waiting,
                "setup_pullback": waiting,
                "setup_entry": waiting,
                "setup_sl": waiting,
                "setup_tp1": waiting,
                "setup_rr": waiting,
                "setup_signal": wrap("WAITING"),
                "market_signal": wrap(ms or "WAITING", methodology="Market classification — not a trade order"),
                "confirmation_strength": wrap(conf_label, methodology="Named confirmations / 10 slots"),
            }

        return {
            "setup_trend": wrap(trend_label, methodology="Confirmed HH/HL or LH/LL structure"),
            "setup_bos": wrap(bos_label, methodology="Close beyond confirmed swing"),
            "setup_impulse": wrap(impulse_label, methodology="ATR/body/RVOL displacement"),
            "setup_pullback": wrap(pb_label, methodology="Retracement after impulse"),
            "setup_entry": wrap(entry.get("entry_price"), methodology=str(entry.get("entry_type"))),
            "setup_sl": wrap(stop.get("final_stop") if stop else None, methodology="Structure + ATR buffer"),
            "setup_tp1": wrap(tp1, methodology="Structural / R-multiple target"),
            "setup_rr": wrap(best_r, methodology="reward/risk screening param"),
            "setup_signal": wrap(status, methodology="Structured setup state — not a prediction"),
            "market_signal": wrap(
                ms,
                methodology=str(cached.get("market_signal_reason") or "Explainable market classification"),
            ),
            "confirmation_strength": wrap(
                conf_label,
                methodology="confirmation_strength = named PASS conditions / 10 (not win probability)",
            ),
        }

    def metrics(self) -> dict[str, Any]:
        lat = self.calc_latencies_ms
        return {
            "signal_calculation_ms_avg": round(sum(lat) / len(lat), 3) if lat else None,
            "signal_calculation_ms_last": round(lat[-1], 3) if lat else None,
            "symbols_recomputed": self.symbols_recomputed,
            "timeframes_recomputed": self.timeframes_touched,
            "queue_depth": self.queue_depth,
            "signals_updated": self.signals_updated,
            "cache_size": len(engine_store.setup_signals),
        }


_setup_service: SetupSignalService | None = None


def get_setup_signal_service() -> SetupSignalService:
    global _setup_service
    if _setup_service is None:
        _setup_service = SetupSignalService()
    return _setup_service
