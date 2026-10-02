"""Explainable technical rating — every component visible. No black-box Strong Buy."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.models.schemas import DataStatus, FreshValue
from app.services.metric_dependencies import tech_rating_requires


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


class TechRatingEngine:
    SOURCE = "tech_rating"

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        weights = dict((cfg.get("signals") or {}).get("weights") or {})
        self.weights = {
            "structure": float(weights.get("structure", 0.25)),
            "supply_demand": float(weights.get("supply_demand", 0.20)),
            "volume": float(weights.get("volume", 0.20)),
            "liquidation": float(weights.get("liquidation", 0.15)),
            "open_interest": float(weights.get("open_interest", 0.20)),
        }
        # Optional momentum weight split from volume if configured
        mtf = dict(cfg.get("tech_rating") or {})
        self.momentum_weight = float(mtf.get("momentum_weight", 0.0))
        labels = dict(mtf.get("labels") or {})
        self.label_thresholds = {
            "bullish": float(labels.get("bullish", 0.65)),
            "bearish": float(labels.get("bearish", 0.35)),
        }

    def evaluate(
        self,
        *,
        structure: str | None,
        rvol: float | None,
        rsi: float | None,
        zone: str | None,
        oi_classification: str | None,
        liquidation_status: str | None,
        bos: str | None = None,
    ) -> dict[str, Any]:
        components: dict[str, dict[str, Any]] = {}

        # Structure 0..1
        if structure is None:
            components["structure"] = {
                "score": None,
                "note": "WAITING — Requires 15m OHLCV / Structure",
            }
        else:
            s = structure.lower()
            score = 0.75 if s in {"bullish", "bull"} else 0.25 if s in {"bearish", "bear"} else 0.5
            if bos and "BULLISH" in bos.upper():
                score = _clamp(score + 0.1)
            if bos and "BEARISH" in bos.upper():
                score = _clamp(score - 0.1)
            components["structure"] = {"score": score, "note": f"structure={structure}"}

        # Volume / RVOL
        if rvol is None:
            components["volume"] = {
                "score": None,
                "note": "WAITING — Requires 15m OHLCV / RVOL",
            }
        else:
            components["volume"] = {
                "score": _clamp(rvol / 2.0),
                "note": f"rvol={rvol:.3f}",
            }

        # Momentum via RSI (optional component)
        if rsi is None:
            components["momentum"] = {
                "score": None,
                "note": "WAITING — Requires 15m OHLCV / RSI",
            }
        else:
            # Neutral mid; oversold slightly bullish for mean-reversion bias is NOT assumed —
            # score mid-high when RSI 40-60 trend continuation zone
            if 45 <= rsi <= 65:
                m = 0.65
            elif rsi > 70:
                m = 0.35
            elif rsi < 30:
                m = 0.40
            else:
                m = 0.50
            components["momentum"] = {"score": m, "note": f"rsi={rsi:.2f}"}

        # Supply/Demand
        if zone is None:
            components["supply_demand"] = {
                "score": None,
                "note": "WAITING — Requires 15m OHLCV / Zones",
            }
        else:
            z = zone.upper()
            if "DEMAND" in z and "FRESH" in z:
                sd = 0.7
            elif "SUPPLY" in z and "FRESH" in z:
                sd = 0.3
            else:
                sd = 0.5
            components["supply_demand"] = {"score": sd, "note": zone}

        # OI descriptive
        if oi_classification is None:
            components["open_interest"] = {
                "score": None,
                "note": "WAITING — Requires OI",
            }
        else:
            mapping = {
                "PRICE_UP_OI_UP": 0.65,
                "PRICE_UP_OI_DOWN": 0.40,
                "PRICE_DOWN_OI_UP": 0.35,
                "PRICE_DOWN_OI_DOWN": 0.45,
            }
            components["open_interest"] = {
                "score": mapping.get(oi_classification, 0.5),
                "note": oi_classification,
            }

        # Liquidations — only score when LIVE
        if liquidation_status is None or liquidation_status == "WAITING":
            components["liquidation"] = {
                "score": None,
                "note": "WAITING — Requires liquidation stream events",
            }
        elif liquidation_status == "LIVE":
            components["liquidation"] = {
                "score": 0.5,
                "note": "Events present (neutral until imbalance wired into rating)",
            }
        else:
            components["liquidation"] = {
                "score": None,
                "note": f"status={liquidation_status}",
            }

        # Weighted aggregate only from available components
        weight_map = {
            "structure": self.weights["structure"],
            "volume": self.weights["volume"],
            "supply_demand": self.weights["supply_demand"],
            "open_interest": self.weights["open_interest"],
            "liquidation": self.weights["liquidation"],
            "momentum": self.momentum_weight,
        }
        num = 0.0
        den = 0.0
        missing = []
        for key, meta in components.items():
            w = weight_map.get(key, 0.0)
            if w <= 0:
                continue
            sc = meta.get("score")
            if sc is None:
                missing.append(key)
                continue
            num += float(sc) * w
            den += w

        ts = datetime.now(timezone.utc)
        requires = tech_rating_requires(missing)
        if den <= 0:
            label = FreshValue.waiting(
                self.SOURCE,
                methodology=requires,
            )
            aggregate = None
        else:
            aggregate = num / den
            if len(missing) >= 3:
                label_str = "INSUFFICIENT_DATA"
                status = DataStatus.WAITING
            elif aggregate >= self.label_thresholds["bullish"]:
                label_str = "BULLISH_BIAS"
                status = DataStatus.LIVE
            elif aggregate <= self.label_thresholds["bearish"]:
                label_str = "BEARISH_BIAS"
                status = DataStatus.LIVE
            else:
                label_str = "NEUTRAL"
                status = DataStatus.LIVE
            # Never emit Strong Buy / Strong Sell marketing terms
            label = FreshValue(
                value=label_str,
                timestamp=ts,
                source=self.SOURCE,
                status=status,
                methodology=(
                    f"{requires}. Weighted components from indicators.yaml signals.weights; "
                    "labels are BULLISH_BIAS/NEUTRAL/BEARISH_BIAS/INSUFFICIENT_DATA only"
                ),
            )

        return {
            "label": label,
            "aggregate_score": aggregate,
            "components": components,
            "weights_used": {k: v for k, v in weight_map.items() if v > 0},
            "missing_components": missing,
        }
