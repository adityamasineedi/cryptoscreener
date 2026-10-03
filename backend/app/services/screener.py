from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.engines.filters.engine import apply_filters
from app.engines.orchestrator import get_orchestrator
from app.engines.signals.entry_exit import EntryExitEngine
from app.engines.signals.tech_rating import TechRatingEngine
from app.engines.valuation.engine import ValuationEngine
from app.ingestion.providers.onchain import OnChainProvider
from app.ingestion.providers.sentiment import SentimentProvider, get_sentiment_provider
from app.models.schemas import DataStatus, FreshValue, ScreenerRow, SymbolInfo
from app.services.asset_metadata import asset_registry
from app.services.engine_store import engine_store
from app.services.freshness import wrap_value
from app.services.historical_performance import compute_performance_sync
from app.services.market_store import MarketDataStore
from app.services.persistence import persistence
from app.services.presets import get_preset, list_presets
from app.services.volume_change import compute_volume_changes


class ScreenerService:
    def __init__(self, settings: Settings, store: MarketDataStore) -> None:
        self.settings = settings
        self.store = store
        self.valuation = ValuationEngine()
        self.entry_exit = EntryExitEngine(settings.indicators_config)
        self.tech_rating = TechRatingEngine(settings.indicators_config)
        self.onchain = OnChainProvider(settings)
        orch = get_orchestrator()
        self.sentiment = (
            orch.sentiment
            if orch is not None and getattr(orch, "sentiment", None) is not None
            else get_sentiment_provider(settings)
        )

    def build_row(self, symbol: SymbolInfo, rank: int | None = None) -> ScreenerRow:
        ticker = self.store.get_ticker(symbol.symbol)
        mark = self.store.mark_prices.get(symbol.symbol)
        stale_ws = self.settings.stale_ticker_seconds
        unavail_ws = self.settings.unavailable_after_seconds
        # REST batch fallback polls on a multi-second cadence — do not apply WS 15s/60s.
        stale_rest = max(stale_ws * 8, 120)
        unavail_rest = max(unavail_ws * 10, 600)
        orch = get_orchestrator()

        def _ticker_windows(source: str) -> tuple[float, float]:
            src = (source or "").lower()
            if "rest" in src:
                return float(stale_rest), float(unavail_rest)
            return float(stale_ws), float(unavail_ws)

        if ticker is None:
            price = FreshValue.waiting("binance_ws")
            change = FreshValue.waiting("binance_ws")
            vol = FreshValue.waiting("binance_ws")
            qvol = FreshValue.waiting("binance_ws")
            high = FreshValue.waiting("binance_ws")
            low = FreshValue.waiting("binance_ws")
            price_val = None
        else:
            t_stale, t_unavail = _ticker_windows(ticker.source)
            price = wrap_value(
                ticker.price, ticker.timestamp, ticker.source,
                stale_after=t_stale, unavailable_after=t_unavail,
            )
            change = wrap_value(
                ticker.price_change_pct_24h, ticker.timestamp, ticker.source,
                stale_after=t_stale, unavailable_after=t_unavail,
            )
            vol = wrap_value(
                ticker.volume_24h, ticker.timestamp, ticker.source,
                stale_after=t_stale, unavailable_after=t_unavail,
            )
            qvol = wrap_value(
                ticker.quote_volume_24h, ticker.timestamp, ticker.source,
                stale_after=t_stale, unavailable_after=t_unavail,
            )
            high = wrap_value(
                ticker.high_24h, ticker.timestamp, ticker.source,
                stale_after=t_stale, unavailable_after=t_unavail,
            )
            low = wrap_value(
                ticker.low_24h, ticker.timestamp, ticker.source,
                stale_after=t_stale, unavailable_after=t_unavail,
            )
            price_val = ticker.price

        if mark is None:
            funding = FreshValue.waiting("binance_ws")
        else:
            m_stale, m_unavail = _ticker_windows(mark.source)
            funding = wrap_value(
                mark.funding_rate, mark.timestamp, mark.source,
                stale_after=m_stale, unavailable_after=m_unavail,
            )

        # OI from scheduler
        oi = FreshValue.waiting("binance_rest")
        oi_chg = FreshValue.waiting("calc")
        oi_chg_24h = FreshValue.waiting("calc")
        if orch is not None:
            st = orch.oi.get_state(symbol.symbol)
            if st is not None:
                oi = st.open_interest
                oi_chg = st.oi_change_pct
                # Prefer 24h window from history classification payload if present
                changes = getattr(st, "changes", None)
                if isinstance(changes, dict) and changes.get("24h") is not None:
                    oi_chg_24h = FreshValue.live(float(changes["24h"]), "calc")
                else:
                    oi_chg_24h = oi_chg

        rvol = engine_store.get_rvol(symbol.symbol, "15m")
        volatility = engine_store.get_volatility(symbol.symbol, "15m")
        structure = engine_store.get_structure_label(symbol.symbol, "15m")
        bos, choch = engine_store.get_bos_choch(symbol.symbol, "15m")
        supply_z, demand_z = engine_store.nearest_zones(symbol.symbol, price_val)

        market_cap = engine_store.get_fundamental(symbol.symbol, "market_cap")
        fdv = engine_store.get_fundamental(symbol.symbol, "fdv")
        tvl = engine_store.get_fundamental(symbol.symbol, "tvl")
        circ = engine_store.get_fundamental(symbol.symbol, "circulating_supply")
        total_sup = engine_store.get_fundamental(symbol.symbol, "total_supply")
        max_sup = engine_store.get_fundamental(symbol.symbol, "max_supply")
        category = engine_store.get_fundamental(symbol.symbol, "category")
        market_rank = engine_store.get_fundamental(symbol.symbol, "market_rank")
        if market_rank.value is None and market_rank.status == DataStatus.WAITING:
            market_rank = FreshValue.waiting(
                "coingecko",
                methodology=(
                    "CoinGecko market_cap_rank (provider methodology) "
                    "— not screener table position"
                ),
            )

        inds = (engine_store.indicators.get(symbol.symbol.upper()) or {}).get("15m") or {}
        from app.services.metric_dependencies import annotate_indicator_waiting

        williams = inds.get("williams_r") or annotate_indicator_waiting(
            symbol.symbol,
            "mtf_engine",
            name="Williams %R",
        )
        if isinstance(williams, FreshValue) and williams.methodology is None:
            williams = FreshValue(
                value=williams.value,
                timestamp=williams.timestamp,
                source=williams.source,
                status=williams.status,
                methodology="(HH_n - Close) / (HH_n - LL_n) * -100",
            )
        rsi_fv = inds.get("rsi")

        # True NVT/velocity only when on-chain tx volume exists (never trading volume)
        on_chain_tx = engine_store.get_fundamental(symbol.symbol, "on_chain_tx_volume")
        if on_chain_tx.value is None:
            on_chain_tx = None

        valuation = self.valuation.compute(
            market_cap=market_cap,
            fdv=fdv,
            quote_volume_24h=qvol,
            circulating_supply=circ,
            total_supply=total_sup,
            max_supply=max_sup,
            on_chain_tx_volume=on_chain_tx,
            price=price,
        )
        # Prefer resolved FDV (max_supply × price when provider FDV≈mcap with supply gap)
        if isinstance(valuation.get("fdv"), FreshValue) and valuation["fdv"].value is not None:
            fdv = valuation["fdv"]

        # Zero TVL is not a useful claim (e.g. BTC) — surface as N/A
        if (
            isinstance(tvl, FreshValue)
            and tvl.value is not None
            and float(tvl.value) == 0.0
        ):
            tvl = FreshValue.unavailable(
                tvl.source or "defillama",
                methodology="N/A — zero / not applicable (no DeFi TVL)",
            )

        vol_chg = compute_volume_changes(symbol.symbol)
        perf = compute_performance_sync(symbol.symbol, price_now=price_val)

        long_liq = FreshValue.waiting(
            "binance_ws", methodology="Requires liquidation stream events"
        )
        short_liq = FreshValue.waiting(
            "binance_ws", methodology="Requires liquidation stream events"
        )
        liq_summary = FreshValue.waiting(
            "binance_ws", methodology="Requires liquidation stream events"
        )
        liq_status = DataStatus.WAITING
        oi_class = None
        if orch is not None:
            liq_summary = orch.liquidations.get_summary(symbol.symbol)
            liq_status = orch.liquidations.liquidation_status()
            aggs = orch.liquidations.aggregates(symbol.symbol)
            w5 = aggs.get("5m") or {}
            if w5 and liq_status == DataStatus.LIVE:
                long_liq = FreshValue.live(
                    float(w5.get("long_liq_notional", 0)), "binance_ws"
                )
                short_liq = FreshValue.live(
                    float(w5.get("short_liq_notional", 0)), "binance_ws"
                )
            ost = orch.oi.get_state(symbol.symbol)
            if ost is not None and ost.oi_classification.value is not None:
                oi_class = str(ost.oi_classification.value)

        if oi.value is None or oi.status in (DataStatus.WAITING, DataStatus.UNAVAILABLE):
            oi = FreshValue(
                value=None,
                timestamp=oi.timestamp,
                source=oi.source or "binance_rest",
                status=oi.status
                if oi.status in (DataStatus.WAITING, DataStatus.UNAVAILABLE)
                else DataStatus.WAITING,
                methodology=oi.methodology or "Requires OI REST coverage",
            )

        zone = supply_z if supply_z.status == DataStatus.LIVE else demand_z
        ee = self.entry_exit.evaluate(
            structure=structure.value if structure.status == DataStatus.LIVE else None,
            bos=bos.value if bos.status == DataStatus.LIVE else None,
            choch=choch.value if choch.status == DataStatus.LIVE else None,
            rvol=float(rvol.value) if rvol.value is not None else None,
            rsi=float(rsi_fv.value) if isinstance(rsi_fv, FreshValue) and rsi_fv.value is not None else None,
            nearest_demand=demand_z.value if demand_z.status == DataStatus.LIVE else None,
            nearest_supply=supply_z.value if supply_z.status == DataStatus.LIVE else None,
            oi_classification=oi_class,
        )
        rating = self.tech_rating.evaluate(
            structure=structure.value if structure.status == DataStatus.LIVE else None,
            rvol=float(rvol.value) if rvol.value is not None else None,
            rsi=float(rsi_fv.value) if isinstance(rsi_fv, FreshValue) and rsi_fv.value is not None else None,
            zone=zone.value if zone.status == DataStatus.LIVE else None,
            oi_classification=oi_class,
            liquidation_status=liq_status.value,
            bos=bos.value if bos.status == DataStatus.LIVE else None,
        )
        tech = rating["label"]
        entry_state = ee["state"]

        statuses = [
            price.status, change.status, qvol.status, funding.status,
            oi.status, rvol.status, structure.status,
        ]
        if any(s == DataStatus.LIVE for s in statuses):
            data_status = DataStatus.LIVE
        elif any(s == DataStatus.WAITING for s in statuses):
            data_status = DataStatus.WAITING
        else:
            data_status = DataStatus.UNAVAILABLE

        from app.services.setup_signals import get_setup_signal_service

        # Cache-first. Missing/stale fills via candle-close, warm, DB hydrate,
        # and visible-symbol ensure queue — never a full-universe sweep here.
        setup_fields = get_setup_signal_service().screener_fields(symbol.symbol)
        signal_payload = {
            "entry_exit": ee,
            "tech_rating": rating,
            "valuation": {
                k: v.model_dump(mode="json") for k, v in valuation.items()
            },
            "setup": engine_store.get_setup_signal(symbol.symbol),
        }
        engine_store.set_signals(symbol.symbol, signal_payload)
        ee_state = ee.get("state") if isinstance(ee, dict) else None
        if ee_state is not None:
            persistence.queue_signal(
                symbol=symbol.symbol,
                timeframe="15m",
                state=str(ee_state.value if hasattr(ee_state, "value") else ee_state),
                reasons=list((ee.get("entry_conditions") or [])[:20])
                if isinstance(ee, dict)
                else [],
                scores={
                    "tech_rating": (
                        rating.get("aggregate_score")
                        if isinstance(rating, dict)
                        else None
                    )
                },
            )

        return ScreenerRow(
            rank=rank,
            screener_rank=rank,
            market_rank=market_rank
            if isinstance(market_rank, FreshValue)
            else FreshValue.waiting("coingecko"),
            symbol=symbol.symbol,
            base_asset=symbol.base_asset,
            quote_asset=symbol.quote_asset,
            exchange=symbol.exchange,
            market_type=symbol.market_type,
            price=price,
            change_24h_pct=change,
            volume_24h=vol,
            quote_volume_24h=qvol,
            volume_change_pct=vol_chg.get("volume_change_24h")
            or FreshValue.waiting("ohlcv_store"),
            volume_change_1h=vol_chg["volume_change_1h"],
            volume_change_4h=vol_chg["volume_change_4h"],
            volume_change_24h=vol_chg["volume_change_24h"],
            volume_change_7d=vol_chg["volume_change_7d"],
            performance_1d=perf["performance_1d"],
            performance_7d=perf["performance_7d"],
            performance_30d=perf["performance_30d"],
            performance_90d=perf["performance_90d"],
            performance_180d=perf["performance_180d"],
            performance_1y=perf["performance_1y"],
            performance_ytd=perf["performance_ytd"],
            high_24h=high,
            low_24h=low,
            funding_rate=funding,
            open_interest=oi,
            oi_change_pct=oi_chg,
            oi_change_24h=oi_chg_24h,
            market_cap=market_cap,
            fdv=fdv,
            circulating_supply=circ,
            total_supply=total_sup,
            max_supply=max_sup,
            category=category if isinstance(category, FreshValue) else FreshValue.waiting("coingecko"),
            tvl=tvl,
            volume_mcap=valuation["volume_mcap"],
            mcap_fdv=valuation["mcap_fdv"],
            nvt=valuation["nvt"],
            velocity=valuation["velocity"],
            williams_r=williams if isinstance(williams, FreshValue) else FreshValue.waiting("mtf_engine"),
            relative_volume=rvol,
            volatility=volatility,
            structure=structure,
            market_structure=structure,
            bos=bos,
            choch=choch,
            nearest_supply_zone=supply_z,
            nearest_demand_zone=demand_z,
            zone=zone,
            liquidation=liq_summary,
            long_liquidations=long_liq,
            short_liquidations=short_liq,
            liquidation_status=liq_status,
            technical_state=tech,
            entry_exit_state=entry_state,
            tech_rating=tech,
            setup_trend=setup_fields["setup_trend"],
            setup_bos=setup_fields["setup_bos"],
            setup_impulse=setup_fields["setup_impulse"],
            setup_pullback=setup_fields["setup_pullback"],
            setup_entry=setup_fields["setup_entry"],
            setup_sl=setup_fields["setup_sl"],
            setup_tp1=setup_fields["setup_tp1"],
            setup_rr=setup_fields["setup_rr"],
            setup_signal=setup_fields["setup_signal"],
            market_signal=setup_fields.get("market_signal")
            or FreshValue.waiting("setup_signal_engine"),
            confirmation_strength=setup_fields.get("confirmation_strength")
            or FreshValue.waiting("setup_signal_engine"),
            social_dominance=(
                engine_store.get_sentiment_field(symbol.symbol, "social_dominance")
                if engine_store.sentiment.get(symbol.symbol.upper())
                else self.sentiment.cached_social_dominance(symbol.symbol)
            ),
            data_status=data_status,
            updated_at=datetime.now(timezone.utc),
        )

    def futures_screener(
        self,
        *,
        search: str | None = None,
        min_change: float | None = None,
        max_change: float | None = None,
        min_volume: float | None = None,
        min_funding: float | None = None,
        max_funding: float | None = None,
        filters: list[dict[str, Any]] | None = None,
        preset: str | None = None,
        sort_by: str = "buy_opportunity",
        limit: int = 100,
        offset: int = 0,
        screen_filter: str | None = None,
        signal: str | None = None,
        setup: str | None = None,
        apply_screen_universe: bool = True,
        force_screen_refresh: bool = False,
    ) -> tuple[list[ScreenerRow], int, dict[str, Any]]:
        """Build screener page.

        Full data universe is still listed/built from market_store. The main
        screener display is capped via screen_universe (max 100) using existing
        row fields only — signal/structure/entry math is untouched.
        """
        from app.services.screen_universe import (
            MAX_SCREEN_SYMBOLS,
            clamp_screen_limit,
            get_screen_universe_selector,
            matches_screen_filter,
            normalize_screen_filter,
        )
        from app.services.setup_signals import get_setup_signal_service

        symbols = self.store.list_symbols(market_type="futures_perp")
        total_universe = len(symbols)
        search_q = (search or "").strip().upper() or None

        # Search narrows the build set so any Binance symbol can be found even
        # when it is outside the dynamic top-100 screen universe.
        build_symbols = symbols
        if search_q:
            build_symbols = [
                s
                for s in symbols
                if search_q in s.symbol or search_q in s.base_asset.upper()
            ]

        rows = [self.build_row(s) for s in build_symbols]

        def val(row: ScreenerRow, field: str):
            fv = getattr(row, field, None)
            return fv.value if fv is not None and hasattr(fv, "value") else None

        # Legacy simple filters
        filtered: list[ScreenerRow] = []
        for row in rows:
            ch = val(row, "change_24h_pct")
            vol = val(row, "quote_volume_24h")
            fr = val(row, "funding_rate")
            if min_change is not None and (ch is None or ch < min_change):
                continue
            if max_change is not None and (ch is None or ch > max_change):
                continue
            if min_volume is not None and (vol is None or vol < min_volume):
                continue
            if min_funding is not None and (fr is None or fr < min_funding):
                continue
            if max_funding is not None and (fr is None or fr > max_funding):
                continue
            filtered.append(row)

        merged_filters = list(filters or [])
        if preset:
            p = get_preset(self.settings, preset)
            if p:
                merged_filters = list(p.get("filters") or []) + merged_filters
        if merged_filters:
            as_dicts = [r.model_dump(mode="json") for r in filtered]
            kept = apply_filters(as_dicts, merged_filters)
            kept_syms = {r["symbol"] for r in kept}
            filtered = [r for r in filtered if r.symbol in kept_syms]

        # Optional API signal/setup filters (existing fields only)
        if signal:
            sig_u = signal.strip().upper()
            next_rows: list[ScreenerRow] = []
            for row in filtered:
                ms = str(val(row, "market_signal") or "WAITING").upper()
                if sig_u == "BUY" and ms in {"BUY", "STRONG_BUY"}:
                    next_rows.append(row)
                elif sig_u == "SELL" and ms in {"SELL", "STRONG_SELL"}:
                    next_rows.append(row)
                elif sig_u == ms:
                    next_rows.append(row)
            filtered = next_rows
        if setup:
            setup_u = setup.strip().upper()
            filtered = [
                r
                for r in filtered
                if str(val(r, "setup_signal") or "WAITING").upper() == setup_u
            ]

        # Map legacy signal/setup into screen_filter when provided alone
        effective_screen_filter = screen_filter
        if not effective_screen_filter and signal:
            su = signal.strip().upper()
            if su in {"BUY", "STRONG_BUY"}:
                effective_screen_filter = "BUY_BIAS"
            elif su in {"SELL", "STRONG_SELL"}:
                effective_screen_filter = "SELL_BIAS"
            elif su == "WAITING":
                effective_screen_filter = "WAITING"
        if not effective_screen_filter and setup:
            su = setup.strip().upper()
            if su in {
                "ENTRY_READY",
                "LONG_ENTRY_CANDIDATE",
                "SHORT_ENTRY_CANDIDATE",
                "ENTRY_CANDIDATE",
            }:
                effective_screen_filter = "ENTRY_READY"
            elif su == "CONFLICT":
                effective_screen_filter = "CONFLICT"
            elif su == "WAITING":
                effective_screen_filter = "WAITING"
            elif su in {"NO_SETUP", "INVALIDATED"}:
                effective_screen_filter = "SETUPS"

        screen_limit = (
            clamp_screen_limit(limit)
            if apply_screen_universe
            else max(1, min(int(limit or MAX_SCREEN_SYMBOLS), 1000))
        )

        meta: dict[str, Any] = {
            "total_universe": total_universe,
            "eligible_count": 0,
            "returned_count": 0,
            "limit": screen_limit,
            "selection_updated_at": None,
            "excluded": {},
            "search_mode": bool(search_q),
            "screen_filter": normalize_screen_filter(effective_screen_filter),
            "max_screen_symbols": MAX_SCREEN_SYMBOLS,
        }

        if apply_screen_universe:
            setup_svc = get_setup_signal_service()
            selection = get_screen_universe_selector().select(
                filtered,
                total_universe=total_universe,
                limit=screen_limit,
                screen_filter=effective_screen_filter or "ALL_ELIGIBLE",
                search=search_q,
                ohlcv_ready_fn=setup_svc.setup_ohlcv_ready,
                force_refresh=force_screen_refresh,
                apply_screen_cap=True,
            )
            filtered = selection.rows
            meta.update(
                {
                    "total_universe": selection.total_universe,
                    "eligible_count": selection.eligible_count,
                    "returned_count": selection.returned_count,
                    "limit": selection.limit,
                    "selection_updated_at": selection.selection_updated_at,
                    "excluded": selection.excluded,
                    "search_mode": selection.search_mode,
                    "screen_filter": selection.screen_filter,
                }
            )
            # Selection already ordered by screen_priority; optional display re-sort
            display_sort = sort_by not in {
                None,
                "",
                "buy_opportunity",
                "market_signal",
                "screen_priority",
            }
        else:
            # Legacy path (e.g. diagnostics): no screen-universe selection
            filt_norm = normalize_screen_filter(effective_screen_filter)
            if filt_norm != "ALL_ELIGIBLE":
                filtered = [r for r in filtered if matches_screen_filter(r, filt_norm)]
            meta["eligible_count"] = len(filtered)
            display_sort = True

        reverse = True
        sort_key_map = {
            "quote_volume_24h": "quote_volume_24h",
            "volume_24h": "volume_24h",
            "change_24h_pct": "change_24h_pct",
            "price": "price",
            "funding_rate": "funding_rate",
            "open_interest": "open_interest",
            "relative_volume": "relative_volume",
            "market_cap": "market_cap",
            "symbol": None,
            "market_signal": "buy_opportunity",
            "buy_opportunity": "buy_opportunity",
            "screen_priority": "screen_priority",
        }
        key = sort_key_map.get(sort_by, "buy_opportunity")

        if apply_screen_universe and not display_sort:
            # Keep screen_priority order from selector
            pass
        elif key is None:
            filtered.sort(key=lambda r: r.symbol)
        elif key == "screen_priority":
            filtered.sort(
                key=lambda r: (
                    r.screen_priority_score is not None,
                    r.screen_priority_score or 0,
                ),
                reverse=True,
            )
        elif key == "buy_opportunity":
            # Display sort only — does not change underlying signals
            signal_rank = {
                "STRONG_BUY": 6,
                "BUY": 5,
                "NEUTRAL": 3,
                "WAITING": 2,
                "SELL": 1,
                "STRONG_SELL": 0,
            }

            def _conf_num(row: ScreenerRow) -> float:
                raw = val(row, "confirmation_strength")
                if raw is None:
                    return -1.0
                if isinstance(raw, (int, float)):
                    return float(raw)
                s = str(raw)
                if "/" in s:
                    try:
                        return float(s.split("/", 1)[0])
                    except ValueError:
                        return -1.0
                try:
                    return float(s)
                except ValueError:
                    return -1.0

            def _buy_key(row: ScreenerRow) -> tuple:
                sig = str(val(row, "market_signal") or "").upper()
                return (
                    signal_rank.get(sig, -1),
                    _conf_num(row),
                    val(row, "quote_volume_24h") or 0,
                )

            filtered.sort(key=_buy_key, reverse=True)
        else:
            filtered.sort(
                key=lambda r: (val(r, key) is not None, val(r, key) or 0),
                reverse=reverse,
            )

        if apply_screen_universe:
            # Selector already applied limit; honor offset within selected set
            total = meta["eligible_count"]
            page = filtered[offset : offset + screen_limit]
        else:
            total = len(filtered)
            page = filtered[offset : offset + screen_limit]

        for i, row in enumerate(page, start=offset + 1):
            row.rank = i
            row.screener_rank = i
        meta["returned_count"] = len(page)
        return page, total, meta

    def row_patch_changes(self, prev: dict[str, Any] | None, row: ScreenerRow) -> dict[str, Any]:
        """Compute incremental field changes for WS row_patch."""
        current = row.model_dump(mode="json")
        if not prev:
            return current
        changes: dict[str, Any] = {}
        for k, v in current.items():
            if k in {"symbol", "base_asset", "quote_asset", "exchange", "market_type"}:
                continue
            if prev.get(k) != v:
                changes[k] = v
        return changes

    @staticmethod
    def _dump_nested(obj: Any) -> Any:
        if isinstance(obj, FreshValue):
            return obj.model_dump(mode="json")
        if isinstance(obj, dict):
            return {k: ScreenerService._dump_nested(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [ScreenerService._dump_nested(v) for v in obj]
        return obj

    async def coin_detail_tabs(self, symbol: str) -> dict[str, Any]:
        """Data-driven analytical tabs for coin detail. Never fabricates metrics."""
        sym = symbol.upper()
        info = self.store.symbols.get(sym)
        if info is None:
            return {
                "symbol": sym,
                "status": "UNAVAILABLE",
                "message": "Symbol not in universe",
            }

        row = self.build_row(info)
        signals = self._dump_nested(engine_store.signals.get(sym) or {})
        inds = {
            tf: {k: v.model_dump(mode="json") for k, v in vals.items()}
            for tf, vals in (engine_store.indicators.get(sym) or {}).items()
        }
        structure = engine_store.structure.get(sym)
        zones = engine_store.zones.get(sym)

        holders = (await self.onchain.get_holder_metrics([sym])).get(sym) or {}
        txs = (await self.onchain.get_transaction_metrics([sym])).get(sym) or {}
        sent = (await self.sentiment.get_sentiment([sym])).get(sym) or {}

        valuation = signals.get("valuation") or {
            k: v.model_dump(mode="json")
            for k, v in self.valuation.compute(
                market_cap=row.market_cap,
                fdv=row.fdv,
                quote_volume_24h=row.quote_volume_24h,
                circulating_supply=row.circulating_supply,
                total_supply=row.total_supply,
                max_supply=row.max_supply,
            ).items()
        }

        performance = {
            "change_24h_pct": row.change_24h_pct.model_dump(mode="json"),
            "performance_1d": row.performance_1d.model_dump(mode="json"),
            "performance_7d": row.performance_7d.model_dump(mode="json"),
            "performance_30d": row.performance_30d.model_dump(mode="json"),
            "performance_90d": row.performance_90d.model_dump(mode="json"),
            "performance_180d": row.performance_180d.model_dump(mode="json"),
            "performance_1y": row.performance_1y.model_dump(mode="json"),
            "performance_ytd": row.performance_ytd.model_dump(mode="json"),
            "volume_change_1h": row.volume_change_1h.model_dump(mode="json"),
            "volume_change_4h": row.volume_change_4h.model_dump(mode="json"),
            "volume_change_24h": row.volume_change_24h.model_dump(mode="json"),
            "volume_change_7d": row.volume_change_7d.model_dump(mode="json"),
            "volatility": row.volatility.model_dump(mode="json"),
            "relative_volume": row.relative_volume.model_dump(mode="json"),
            "williams_r": row.williams_r.model_dump(mode="json"),
            "methodology": (
                "performance: (price_now / price_at_period_start - 1) * 100 from 1d OHLCV; "
                "never substitutes 24h ticker change when history is missing (WAITING)"
            ),
        }

        # Split valuation: production true NVT/velocity vs EXPERIMENTAL proxies
        valuation_prod = {
            k: v
            for k, v in valuation.items()
            if k not in ("nvt_proxy", "velocity_proxy")
        }
        valuation_experimental = {
            k: v
            for k, v in valuation.items()
            if k in ("nvt_proxy", "velocity_proxy")
        }

        return {
            "symbol": sym,
            "status": "LIVE",
            "tabs": {
                "overview": {
                    "row": row.model_dump(mode="json"),
                    "entry_exit": signals.get("entry_exit"),
                    "tech_rating": signals.get("tech_rating"),
                },
                "performance": performance,
                "technicals": {
                    "indicators": inds,
                    "structure": structure,
                    "entry_exit": signals.get("entry_exit"),
                    "tech_rating": signals.get("tech_rating"),
                    "williams_r": row.williams_r.model_dump(mode="json"),
                    "volatility": row.volatility.model_dump(mode="json"),
                    "relative_volume": row.relative_volume.model_dump(mode="json"),
                    "bos": row.bos.model_dump(mode="json"),
                    "choch": row.choch.model_dump(mode="json"),
                },
                "valuation": {
                    **valuation_prod,
                    "market_rank": row.market_rank.model_dump(mode="json"),
                    "screener_rank": row.screener_rank,
                    "EXPERIMENTAL": valuation_experimental,
                },
                "derivatives": {
                    "open_interest": row.open_interest.model_dump(mode="json"),
                    "oi_change_pct": row.oi_change_pct.model_dump(mode="json"),
                    "oi_change_24h": row.oi_change_24h.model_dump(mode="json"),
                    "funding_rate": row.funding_rate.model_dump(mode="json"),
                    "long_liquidations": row.long_liquidations.model_dump(mode="json"),
                    "short_liquidations": row.short_liquidations.model_dump(mode="json"),
                    "liquidation": row.liquidation.model_dump(mode="json"),
                    "liquidation_status": row.liquidation_status.value,
                },
                "addresses": {
                    **{k: v.model_dump(mode="json") for k, v in holders.items()},
                    "asset_metadata": asset_registry.as_dict(sym),
                    "provider": self.onchain.status(),
                    "note": (
                        "Raw concentration metrics only — not a decentralization claim. "
                        "Requires assets.yaml mapping + on-chain provider."
                    ),
                },
                "transactions": {
                    **{k: v.model_dump(mode="json") for k, v in txs.items()},
                    "asset_metadata": asset_registry.as_dict(sym),
                    "provider": self.onchain.status(),
                },
                "sentiment": {
                    **{k: v.model_dump(mode="json") for k, v in sent.items()},
                    "provider": self.sentiment.status(),
                },
                "setup": self._setup_tab(sym),
            },
            "zones": zones,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _setup_tab(self, symbol: str) -> dict[str, Any]:
        from app.services.setup_signals import get_setup_signal_service

        svc = get_setup_signal_service()
        payload = engine_store.get_setup_signal(symbol) or svc.analyze_symbol(symbol)
        ms_payload = payload.get("market_signal_payload") or {}
        return {
            "analysis": payload,
            "market_signal": payload.get("market_signal"),
            "market_signal_reason": payload.get("market_signal_reason"),
            "confirmation_strength": payload.get("confirmation_strength"),
            "confirmation_denominator": payload.get("confirmation_denominator"),
            "bullish_conditions": ms_payload.get("bullish_conditions"),
            "bearish_conditions": ms_payload.get("bearish_conditions"),
            "market_signal_conditions": ms_payload.get("conditions"),
            "market_signal_reasons": ms_payload.get("reasons"),
            "classification_version": payload.get("classification_version"),
            "trend": payload.get("trend"),
            "structure": {
                "bos": payload.get("bos"),
                "choch": payload.get("choch"),
                "swings": payload.get("swings"),
            },
            "setup_state": {
                "impulse": payload.get("impulse"),
                "pullback": payload.get("pullback"),
                "retest": payload.get("retest"),
                "status": payload.get("status"),
            },
            "trade_plan": {
                "entry": payload.get("entry"),
                "stop": payload.get("stop"),
                "targets": payload.get("targets"),
                "risk_reward": payload.get("risk_reward"),
            },
            "risk": payload.get("risk_management"),
            "mtf": payload.get("mtf"),
            "score": payload.get("score"),
            "conditions": payload.get("conditions"),
            "explanation": payload.get("explanation"),
            "annotations": payload.get("annotations"),
            "data_dependencies": payload.get("data_dependencies"),
            "disclaimer": payload.get("disclaimer"),
        }

    def list_presets(self) -> list[dict[str, Any]]:
        return list_presets(self.settings)
