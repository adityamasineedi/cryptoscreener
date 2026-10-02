"""Structure setup signal engine: Trend→BOS→Impulse→Pullback→Entry→Risk.

Uses real OHLCV only. Never fabricates market data. Never claims profitability.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.signals._candle_utils import candle_time, ohlc
from app.signals.bos_engine import detect_bos
from app.signals.choch_engine import detect_choch
from app.signals.config import SignalConfig
from app.signals.entry_engine import evaluate_entry
from app.signals.impulse_engine import detect_impulse
from app.signals.mtf_engine import align_mtf
from app.signals.pullback_engine import detect_pullback
from app.signals.retest_engine import detect_retest
from app.signals.risk_engine import (
    estimate_liquidation_price,
    futures_risk_checks,
    position_size,
    risk_reward,
)
from app.signals.market_signal_engine import classify_market_signal
from app.signals.schemas import SetupAnalysis, SignalStatus, TrendState
from app.signals.stop_engine import compute_stop
from app.signals.swing_detector import swings_for_timeframe
from app.signals.target_engine import compute_targets
from app.signals.trend_engine import infer_trend


def _attach_market_signal(analysis: SetupAnalysis, config: SignalConfig) -> SetupAnalysis:
    """Classify market_signal from existing analysis fields (same as_of path)."""
    payload = classify_market_signal(analysis=analysis.to_dict(), config=config)
    analysis.market_signal = payload.get("market_signal")
    analysis.market_signal_reason = payload.get("market_signal_reason")
    analysis.confirmation_strength = payload.get("confirmation_strength")
    analysis.confirmation_denominator = payload.get("confirmation_denominator")
    analysis.market_signal_payload = payload
    analysis.classification_version = payload.get("classification_version")
    # Compact chart annotation
    if analysis.market_signal and analysis.market_signal not in ("WAITING", "NEUTRAL"):
        analysis.annotations = list(analysis.annotations or []) + [
            {
                "kind": "MARKET_SIGNAL",
                "group": "signal",
                "price": (analysis.entry or {}).get("entry_price")
                if isinstance(analysis.entry, dict)
                else None,
                "label": analysis.market_signal,
            }
        ]
    return analysis


def _source_candle_timestamps(
    candles_by_tf: Mapping[str, Sequence[Mapping[str, Any]]],
    as_of_index_by_tf: Mapping[str, int] | None = None,
) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    as_of_index_by_tf = as_of_index_by_tf or {}
    for tf, candles in candles_by_tf.items():
        if not candles:
            out[tf] = None
            continue
        idx = as_of_index_by_tf.get(tf)
        if idx is None:
            idx = len(candles) - 1
        idx = min(max(idx, 0), len(candles) - 1)
        ts = candle_time(candles[idx])
        out[tf] = ts.isoformat() if ts else None
    return out


class SignalEngine:
    """Explainable multi-step setup analyzer."""

    SOURCE = "setup_signal_engine"

    def __init__(self, config: SignalConfig | Mapping[str, Any] | None = None) -> None:
        if isinstance(config, SignalConfig):
            self.config = config
        else:
            self.config = SignalConfig.from_mapping(config)

    def analyze_timeframe(
        self,
        symbol: str,
        timeframe: str,
        candles: Sequence[Mapping[str, Any]],
        *,
        rvol: float | None = None,
        demand_zone: tuple[float, float] | None = None,
        supply_zone: tuple[float, float] | None = None,
        vwap: float | None = None,
        ema: float | None = None,
        as_of_index: int | None = None,
    ) -> dict[str, Any]:
        if not candles:
            return {
                "timeframe": timeframe,
                "trend": {"trend": TrendState.WAITING.value, "reason": "WAITING FOR OHLCV"},
                "data_status": "WAITING",
            }

        swings = swings_for_timeframe(
            candles,
            self.config,
            timeframe,
            symbol=symbol,
            as_of_index=as_of_index,
        )
        trend = infer_trend(swings)
        bos = detect_bos(
            candles,
            swings,
            trend,
            symbol=symbol,
            timeframe=timeframe,
            atr_period=self.config.atr_period,
            rvol=rvol,
            as_of_index=as_of_index,
        )
        choch = detect_choch(
            candles,
            swings,
            trend,
            symbol=symbol,
            timeframe=timeframe,
            as_of_index=as_of_index,
        )
        impulse = detect_impulse(
            candles, bos, self.config, rvol=rvol, as_of_index=as_of_index
        )
        pullback = detect_pullback(
            candles,
            bos,
            impulse,
            self.config,
            demand_zone=demand_zone,
            supply_zone=supply_zone,
            vwap=vwap,
            ema=ema,
            as_of_index=as_of_index,
        )
        direction = None
        if bos and bos.get("direction") == "BULLISH_BOS":
            direction = "LONG"
        elif bos and bos.get("direction") == "BEARISH_BOS":
            direction = "SHORT"
        retest = detect_retest(
            candles,
            bos,
            pullback,
            self.config,
            direction=direction,
            as_of_index=as_of_index,
        )
        return {
            "timeframe": timeframe,
            "trend": trend,
            "swings": [s.to_dict() for s in swings],
            "bos": bos,
            "choch": choch,
            "impulse": impulse,
            "pullback": pullback,
            "retest": retest,
            "data_status": "LIVE",
            "_swings_objs": swings,
        }

    def analyze(
        self,
        symbol: str,
        candles_by_tf: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        rvol_by_tf: Mapping[str, float | None] | None = None,
        demand_zone: tuple[float, float] | None = None,
        supply_zone: tuple[float, float] | None = None,
        vwap: float | None = None,
        ema: float | None = None,
        oi_status: str = "N/A",
        liquidation_status: str = "N/A",
        account_equity: float | None = None,
        risk_percent: float | None = None,
        leverage: float | None = None,
        liquidation_price: float | None = None,
        as_of_index_by_tf: Mapping[str, int] | None = None,
    ) -> SetupAnalysis:
        cfg = self.config
        setup_tf = cfg.mtf_setup
        rvol_by_tf = rvol_by_tf or {}
        as_of_index_by_tf = as_of_index_by_tf or {}

        per_tf: dict[str, dict[str, Any]] = {}
        trends: dict[str, str | None] = {}
        data_deps: dict[str, str] = {}

        for tf in (cfg.mtf_major, cfg.mtf_primary, cfg.mtf_setup, cfg.mtf_entry, cfg.mtf_refine):
            candles = list(candles_by_tf.get(tf) or [])
            if not candles:
                trends[tf] = TrendState.WAITING.value
                data_deps[tf] = "WAITING FOR OHLCV"
                per_tf[tf] = {
                    "timeframe": tf,
                    "trend": {"trend": TrendState.WAITING.value, "reason": "WAITING FOR OHLCV"},
                    "data_status": "WAITING",
                }
                continue
            analysis = self.analyze_timeframe(
                symbol,
                tf,
                candles,
                rvol=rvol_by_tf.get(tf),
                demand_zone=demand_zone,
                supply_zone=supply_zone,
                vwap=vwap,
                ema=ema,
                as_of_index=as_of_index_by_tf.get(tf),
            )
            per_tf[tf] = analysis
            trends[tf] = str((analysis.get("trend") or {}).get("trend"))
            data_deps[tf] = "LIVE"

        data_deps["OI"] = oi_status if oi_status else "N/A"
        data_deps["Liquidations"] = liquidation_status if liquidation_status else "N/A"

        source_ts = _source_candle_timestamps(candles_by_tf, as_of_index_by_tf)
        calculated_at = datetime.now(timezone.utc).isoformat()

        # If setup TF unavailable — do not invent BOS/impulse/pullback/entry
        setup = per_tf.get(setup_tf) or {}
        if setup.get("data_status") == "WAITING":
            mtf = align_mtf(trends, config=cfg)
            waiting = SetupAnalysis(
                symbol=symbol.upper(),
                status=SignalStatus.WAITING.value,
                direction=None,
                timeframe=setup_tf,
                trend={k: trends.get(k) for k in (cfg.mtf_major, cfg.mtf_primary, cfg.mtf_setup, cfg.mtf_entry)},
                mtf=mtf,
                explanation=[f"{setup_tf.upper()}: WAITING FOR OHLCV — no setup generated"],
                data_dependencies=data_deps,
                conditions=[
                    {
                        "id": "ohlcv",
                        "label": "OHLCV",
                        "verdict": "WAITING",
                        "detail": f"{setup_tf} unavailable",
                    }
                ],
                source_candle_timestamps=source_ts,
                calculated_at=calculated_at,
                signal_status="WAITING",
            )
            return _attach_market_signal(waiting, cfg)

        bos = setup.get("bos")
        choch = setup.get("choch")
        impulse = setup.get("impulse")
        pullback = setup.get("pullback")
        retest = setup.get("retest")
        swings = setup.get("_swings_objs") or []
        setup_trend = setup.get("trend") or {}

        mtf = align_mtf(
            trends,
            setup_bos=(bos or {}).get("direction"),
            entry_trigger=None,
            config=cfg,
        )

        candles_setup = list(candles_by_tf.get(setup_tf) or [])
        end = len(candles_setup) - 1
        last_close = ohlc(candles_setup, end)[3] if end >= 0 else None

        # Provisional entry for SL/TP math
        provisional_entry = None
        direction_hint = None
        if bos and bos.get("direction") == "BULLISH_BOS":
            direction_hint = "LONG"
            provisional_entry = float(bos.get("broken_level") or last_close or 0)
        elif bos and bos.get("direction") == "BEARISH_BOS":
            direction_hint = "SHORT"
            provisional_entry = float(bos.get("broken_level") or last_close or 0)

        atr = (impulse or {}).get("atr") or (bos or {}).get("atr")
        stop = None
        targets: list[dict[str, Any]] = []
        rr = None
        if direction_hint and provisional_entry:
            stop = compute_stop(
                direction=direction_hint,
                entry_price=provisional_entry,
                pullback=pullback,
                demand_zone=demand_zone,
                supply_zone=supply_zone,
                atr=float(atr) if atr else None,
                sl_buffer_atr=cfg.sl_buffer_atr,
            )
            if stop.get("final_stop") is not None:
                targets = compute_targets(
                    direction=direction_hint,
                    entry_price=provisional_entry,
                    stop=stop,
                    swings=swings,
                    supply_zone=supply_zone,
                    demand_zone=demand_zone,
                    config=cfg,
                )
                rr = risk_reward(
                    provisional_entry,
                    float(stop["final_stop"]),
                    targets,
                    min_rr=cfg.min_rr,
                )

        volume_ok = None
        rvol_setup = rvol_by_tf.get(setup_tf)
        if rvol_setup is not None:
            volume_ok = rvol_setup >= cfg.min_rvol
        elif impulse and impulse.get("rvol") is not None:
            volume_ok = float(impulse["rvol"]) >= cfg.min_rvol

        entry = evaluate_entry(
            mtf=mtf,
            setup_trend=setup_trend,
            bos=bos,
            impulse=impulse,
            pullback=pullback,
            retest=retest,
            stop=stop,
            targets=targets,
            risk_reward=rr,
            config=cfg,
            last_close=last_close,
            volume_ok=volume_ok,
            oi_available=oi_status == "LIVE",
            liquidation_available=liquidation_status == "LIVE",
        )

        # Recompute stop/targets with final entry price when available
        direction = entry.get("direction") or direction_hint
        entry_price = entry.get("entry_price")
        if (
            entry_price
            and direction
            and entry.get("status")
            in (
                SignalStatus.LONG_ENTRY_CANDIDATE.value,
                SignalStatus.SHORT_ENTRY_CANDIDATE.value,
                SignalStatus.ENTRY_CANDIDATE.value,
            )
        ):
            stop = compute_stop(
                direction=direction,
                entry_price=float(entry_price),
                pullback=pullback,
                demand_zone=demand_zone,
                supply_zone=supply_zone,
                atr=float(atr) if atr else None,
                sl_buffer_atr=cfg.sl_buffer_atr,
            )
            targets = compute_targets(
                direction=direction,
                entry_price=float(entry_price),
                stop=stop,
                swings=swings,
                supply_zone=supply_zone,
                demand_zone=demand_zone,
                config=cfg,
            )
            if stop.get("final_stop") is not None:
                rr = risk_reward(
                    float(entry_price),
                    float(stop["final_stop"]),
                    targets,
                    min_rr=cfg.min_rr,
                )

        # Invalidation checks
        invalidation = None
        status = str(entry.get("status"))
        if pullback and pullback.get("pullback_state") == "INVALIDATED":
            status = SignalStatus.INVALIDATED.value
            invalidation = pullback.get("reason")
        if choch and choch.get("state") == "CONFIRMED" and bos and bos.get("state") == "CONFIRMED":
            # Opposite character change after setup
            if (
                bos.get("direction") == "BULLISH_BOS"
                and choch.get("direction") == "CHOCH_BEARISH"
            ) or (
                bos.get("direction") == "BEARISH_BOS"
                and choch.get("direction") == "CHOCH_BULLISH"
            ):
                status = SignalStatus.INVALIDATED.value
                invalidation = "Opposite CHOCH after BOS"

        eq = account_equity if account_equity is not None else cfg.default_account_equity
        rp = risk_percent if risk_percent is not None else cfg.default_risk_percent
        lev = leverage if leverage is not None else cfg.default_leverage
        risk_mgmt: dict[str, Any] = {}
        if entry_price and stop and stop.get("final_stop") is not None:
            pos = position_size(
                account_equity=eq,
                risk_percent=rp,
                entry=float(entry_price),
                stop=float(stop["final_stop"]),
                contract_quantity_step=cfg.contract_quantity_step,
                minimum_quantity=cfg.minimum_quantity,
                leverage=lev,
                fee_rate=cfg.fee_rate,
                slippage_rate=cfg.slippage_rate,
            )
            liq = liquidation_price
            if liq is None and direction:
                liq = estimate_liquidation_price(
                    entry=float(entry_price), leverage=lev, direction=direction
                )
            checks = futures_risk_checks(
                entry=float(entry_price),
                stop=float(stop["final_stop"]),
                leverage=lev,
                position=pos,
                config=cfg,
                liquidation_price=liq,
                direction=direction or "LONG",
            )
            risk_mgmt = {**pos, **checks}

        score = self._score(
            setup_trend=setup_trend,
            bos=bos,
            impulse=impulse,
            pullback=pullback,
            volume_ok=volume_ok,
            demand_zone=demand_zone,
            supply_zone=supply_zone,
            oi_status=oi_status,
            liquidation_status=liquidation_status,
        )

        explanation = self._explain(
            symbol=symbol,
            status=status,
            direction=direction,
            trends=trends,
            bos=bos,
            impulse=impulse,
            pullback=pullback,
            retest=retest,
            stop=stop,
            targets=targets,
            rr=rr,
            data_deps=data_deps,
            conditions=entry.get("conditions") or [],
        )

        annotations = self._annotations(
            swings=setup.get("swings") or [],
            bos=bos,
            choch=choch,
            impulse=impulse,
            pullback=pullback,
            entry=entry,
            stop=stop,
            targets=targets,
            direction=direction,
        )

        # Strip internal objects
        for tf_payload in per_tf.values():
            tf_payload.pop("_swings_objs", None)

        result = SetupAnalysis(
            symbol=symbol.upper(),
            status=status,
            direction=direction,
            timeframe=setup_tf,
            trend={
                cfg.mtf_major: trends.get(cfg.mtf_major),
                cfg.mtf_primary: trends.get(cfg.mtf_primary),
                cfg.mtf_setup: trends.get(cfg.mtf_setup),
                cfg.mtf_entry: trends.get(cfg.mtf_entry),
            },
            swings=setup.get("swings") or [],
            bos=bos,
            choch=choch,
            impulse=impulse,
            pullback=pullback,
            retest=retest,
            entry=entry,
            stop=stop,
            targets=targets,
            risk_reward=rr or {},
            risk_management=risk_mgmt,
            mtf=mtf,
            score=score,
            conditions=list(entry.get("conditions") or []),
            explanation=explanation,
            invalidation_reason=invalidation,
            data_dependencies=data_deps,
            annotations=annotations,
            source_candle_timestamps=source_ts,
            calculated_at=calculated_at,
            signal_status="LIVE",
        )
        return _attach_market_signal(result, cfg)

    def _score(
        self,
        *,
        setup_trend: dict[str, Any],
        bos: dict[str, Any] | None,
        impulse: dict[str, Any] | None,
        pullback: dict[str, Any] | None,
        volume_ok: bool | None,
        demand_zone: tuple[float, float] | None,
        supply_zone: tuple[float, float] | None,
        oi_status: str,
        liquidation_status: str,
    ) -> dict[str, Any]:
        w = self.config.score_weights
        components: dict[str, Any] = {}
        total = 0.0
        awarded = 0.0

        def add(name: str, points: float | None, note: str) -> None:
            nonlocal total, awarded
            weight = float(w.get(name, 0))
            if points is None:
                components[name] = {"score": None, "note": note, "verdict": "N/A"}
                return
            total += weight
            awarded += points
            components[name] = {"score": points, "note": note, "verdict": "OK"}

        tr = str(setup_trend.get("trend") or "")
        if tr in ("BULLISH", "BEARISH"):
            add("trend", float(w.get("trend", 20)), tr)
        elif tr in ("WAITING", "INSUFFICIENT_DATA"):
            add("trend", None, tr)
        else:
            add("trend", 0.0, tr)

        if bos and bos.get("state") == "CONFIRMED":
            add("bos", float(w.get("bos", 20)), str(bos.get("direction")))
        else:
            add("bos", 0.0, "no BOS")

        if impulse and impulse.get("is_impulse"):
            add("impulse", float(w.get("impulse", 15)), impulse.get("quality", ""))
        else:
            add("impulse", 0.0, (impulse or {}).get("reason", ""))

        if pullback and pullback.get("pullback_state") in ("ACTIVE", "CONFIRMED"):
            add("pullback", float(w.get("pullback", 15)), pullback.get("pullback_state", ""))
        else:
            add("pullback", 0.0, (pullback or {}).get("pullback_state", ""))

        if volume_ok is None:
            add("volume", None, "RVOL N/A")
        elif volume_ok:
            add("volume", float(w.get("volume", 8)), "RVOL ok")
        else:
            add("volume", 0.0, "RVOL weak")

        if demand_zone or supply_zone:
            add("supply_demand", float(w.get("supply_demand", 4)), "zone present")
        else:
            add("supply_demand", None, "N/A")

        if oi_status == "LIVE":
            add("oi", float(w.get("oi", 0)), "LIVE")
        else:
            add("oi", None, oi_status or "N/A")

        if liquidation_status == "LIVE":
            add("liquidation", float(w.get("liquidation", 0)), "LIVE")
        else:
            add("liquidation", None, liquidation_status or "N/A")

        aggregate = (awarded / total * 100.0) if total > 0 else None
        return {
            "TECHNICAL_SCORE": round(aggregate, 1) if aggregate is not None else None,
            "components": components,
            "note": "Score is explanatory only — not the sole entry decision",
        }

    def _explain(
        self,
        *,
        symbol: str,
        status: str,
        direction: str | None,
        trends: dict[str, str | None],
        bos: dict[str, Any] | None,
        impulse: dict[str, Any] | None,
        pullback: dict[str, Any] | None,
        retest: dict[str, Any] | None,
        stop: dict[str, Any] | None,
        targets: list[dict[str, Any]],
        rr: dict[str, Any] | None,
        data_deps: dict[str, str],
        conditions: list[dict[str, Any]],
    ) -> list[str]:
        lines = [f"{symbol.upper()} — {status}" + (f" ({direction})" if direction else "")]
        lines.append("WHY:")
        for tf, tr in trends.items():
            if tr and tr != "WAITING":
                mark = "✓" if tr in ("BULLISH", "BEARISH") else "•"
                lines.append(f"{mark} {tf.upper()} {tr}")
            else:
                lines.append(f"• {tf.upper()} WAITING FOR OHLCV")
        if bos and bos.get("state") == "CONFIRMED":
            lines.append(f"✓ {bos.get('reason')}")
        if impulse and impulse.get("is_impulse"):
            lines.append(
                f"✓ impulse range = {impulse.get('atr_multiple')} ATR; RVOL = {impulse.get('rvol')}"
            )
        if pullback:
            lines.append(f"• pullback: {pullback.get('pullback_state')} — {pullback.get('reason')}")
        if retest and retest.get("retest"):
            lines.append(f"✓ {retest.get('reason')}")
        if rr:
            lines.append(f"• R:R best = {rr.get('best_R')} ({rr.get('RISK_REWARD')})")
        lines.append("RISK:")
        if stop and stop.get("final_stop") is not None:
            lines.append(f"• Stop = {stop.get('final_stop')}")
        for t in targets:
            lines.append(f"• {t.get('name')} = {t.get('target_price')} ({t.get('structural_reason')})")
        lines.append("DATA:")
        for k, v in data_deps.items():
            lines.append(f"• {k} = {v}")
        lines.append("CONDITIONS:")
        for c in conditions:
            lines.append(f"• {c.get('label')}: {c.get('verdict')}")
        lines.append(
            "Disclaimer: structural setup identification only — not a prediction of profit."
        )
        return lines

    def _annotations(
        self,
        *,
        swings: list[dict[str, Any]],
        bos: dict[str, Any] | None,
        choch: dict[str, Any] | None,
        impulse: dict[str, Any] | None,
        pullback: dict[str, Any] | None,
        entry: dict[str, Any] | None,
        stop: dict[str, Any] | None,
        targets: list[dict[str, Any]],
        direction: str | None = None,
    ) -> list[dict[str, Any]]:
        ann: list[dict[str, Any]] = []
        side = str(
            direction
            or (entry or {}).get("direction")
            or ""
        ).upper()
        for s in swings[-8:]:
            ann.append(
                {
                    "kind": "SWING_HIGH" if s.get("swing_type") == "HIGH" else "SWING_LOW",
                    "group": "structure",
                    "price": s.get("price"),
                    "time": s.get("timestamp"),
                    "label": s.get("label"),
                }
            )
        if bos and bos.get("state") == "CONFIRMED":
            ann.append(
                {
                    "kind": "BOS",
                    "group": "bos_choch",
                    "price": bos.get("broken_level"),
                    "time": bos.get("break_timestamp"),
                    "label": bos.get("direction"),
                }
            )
        if choch and choch.get("state") == "CONFIRMED":
            ann.append(
                {
                    "kind": "CHOCH",
                    "group": "bos_choch",
                    "price": choch.get("level"),
                    "time": choch.get("break_timestamp"),
                    "label": choch.get("direction"),
                }
            )
        if impulse and impulse.get("is_impulse"):
            ann.append(
                {
                    "kind": "IMPULSE",
                    "group": "setup",
                    "price": impulse.get("impulse_end"),
                    "label": impulse.get("quality"),
                }
            )
        if pullback and pullback.get("pullback_state") in ("ACTIVE", "CONFIRMED"):
            ann.append(
                {
                    "kind": "PULLBACK",
                    "group": "setup",
                    "price": pullback.get("retracement_low") or pullback.get("retracement_high"),
                    "label": pullback.get("pullback_state"),
                }
            )
        # Prefer BOS/CHOCH break time so BUY/SELL markers land on a chart bar
        plan_time = None
        if bos and bos.get("break_timestamp"):
            plan_time = bos.get("break_timestamp")
        elif choch and choch.get("break_timestamp"):
            plan_time = choch.get("break_timestamp")
        elif entry and entry.get("timestamp"):
            plan_time = entry.get("timestamp")

        if entry and entry.get("entry_price") is not None:
            # Chart-facing side labels (BUY / SELL) from setup direction
            if side == "SHORT":
                buy_sell_kind = "SELL"
            elif side == "LONG":
                buy_sell_kind = "BUY"
            else:
                buy_sell_kind = "ENTRY"
            ann.append(
                {
                    "kind": buy_sell_kind,
                    "group": "trade_plan",
                    "price": entry.get("entry_price"),
                    "time": plan_time,
                    "label": entry.get("entry_type") or buy_sell_kind,
                    "direction": side or None,
                }
            )
        if stop and stop.get("final_stop") is not None:
            ann.append(
                {
                    "kind": "SL",
                    "group": "trade_plan",
                    "price": stop.get("final_stop"),
                    "time": plan_time,
                    "label": "STOP",
                    "direction": side or None,
                }
            )
        for t in targets:
            tp_name = str(t.get("name") or "TP1")
            ann.append(
                {
                    "kind": "TAKE_PROFIT",
                    "group": "trade_plan",
                    "price": t.get("target_price"),
                    "time": plan_time,
                    "label": tp_name,
                    "direction": side or None,
                }
            )
        return ann
