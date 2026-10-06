"""Explicit BOS research combination definitions.

No combination is assumed superior. Conditions are stored with each backtest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CombinationDefinition:
    combination_id: str
    name: str
    description: str
    require_bos: bool = True
    require_trend: bool = False
    require_impulse: bool = False
    require_pullback: bool = False
    require_rvol: bool = False
    require_sd: bool = False
    require_rr: bool = False
    # Hard 4h+1h structure alignment (fail closed). Independent of live
    # SignalConfig.require_mtf_alignment — research must pass HTF candles.
    require_htf_alignment: bool = False
    # When True with HTF: as-of uses last HTF bar whose close time <= setup
    # bar close (no forming HTF OHLC). Default False preserves COMBO_02 v1.
    htf_require_fully_closed: bool = False
    conditions: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "combination_id": self.combination_id,
            "name": self.name,
            "description": self.description,
            "require_bos": self.require_bos,
            "require_trend": self.require_trend,
            "require_impulse": self.require_impulse,
            "require_pullback": self.require_pullback,
            "require_rvol": self.require_rvol,
            "require_sd": self.require_sd,
            "require_rr": self.require_rr,
            "require_htf_alignment": self.require_htf_alignment,
            "htf_require_fully_closed": self.htf_require_fully_closed,
            "conditions": list(self.conditions),
            "gates": {
                "BOS": self.require_bos,
                "Trend": self.require_trend,
                "Impulse": self.require_impulse,
                "Pullback": self.require_pullback,
                "RVOL": self.require_rvol,
                "S/D": self.require_sd,
                "R:R": self.require_rr,
                "HTF": self.require_htf_alignment,
                "HTF_FULLY_CLOSED": self.htf_require_fully_closed,
            },
            "direction_filter_notes": {
                "setup_tf_only": not self.require_htf_alignment,
                "htf_hard_gate": self.require_htf_alignment,
                "htf_fully_closed_bars": self.htf_require_fully_closed,
            },
        }


COMBINATIONS: dict[str, CombinationDefinition] = {
    "COMBO_01": CombinationDefinition(
        combination_id="COMBO_01",
        name="BOS_ONLY",
        description="BOS confirmed.",
        require_bos=True,
        conditions=("BOS confirmed",),
    ),
    # v1 freeze (tag: v1-combo02-long-htf) — do not weaken HTF; see docs/v1_freeze.md
    "COMBO_02": CombinationDefinition(
        combination_id="COMBO_02",
        name="TREND_BOS",
        description=(
            "BOS confirmed AND setup-TF trend agrees AND 4h+1h HTF "
            "structure aligned with BOS direction (fail closed)."
        ),
        require_bos=True,
        require_trend=True,
        require_htf_alignment=True,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "4h+1h HTF structure aligned (hard gate)",
        ),
    ),
    # Legacy setup-TF-only baseline — research A/B only.
    # NOT COMBO_02 v1 / NOT HTF-gated production; do not use for v1 claims.
    "COMBO_02_LOCAL": CombinationDefinition(
        combination_id="COMBO_02_LOCAL",
        name="TREND_BOS_SETUP_TF_ONLY",
        description=(
            "LEGACY/RESEARCH-ONLY (not COMBO_02 v1): BOS + setup-TF trend only "
            "(no 4h+1h HTF hard gate). For before/after comparison vs COMBO_02."
        ),
        require_bos=True,
        require_trend=True,
        require_htf_alignment=False,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "Setup-TF only (HTF not required) — not v1 production",
        ),
    ),
    # Separate version: same COMBO_02 gates, but HTF trends from fully closed
    # HTF candles only (no forming-4h OHLC at 1h decision time).
    "COMBO_02_CLOSED_HTF": CombinationDefinition(
        combination_id="COMBO_02_CLOSED_HTF",
        name="TREND_BOS_CLOSED_HTF",
        description=(
            "COMBO_02 gates with closed-bar HTF as-of: 4h/1h trends use the last "
            "HTF candle whose close time is <= setup bar close (no forming HTF). "
            "Versioned as COMBO_02_V1_CLOSED_HTF — does not mutate COMBO_02 v1."
        ),
        require_bos=True,
        require_trend=True,
        require_htf_alignment=True,
        htf_require_fully_closed=True,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "4h+1h HTF structure aligned (hard gate)",
            "HTF candles fully closed at setup bar close",
        ),
    ),
    # Adaptive 3-regime research playbook — does NOT mutate COMBO_02 v1.
    # Custom evaluator (combo02_v2); flags off so walk does not use v1 HTF/BOS prefilter.
    "COMBO_02_V2": CombinationDefinition(
        combination_id="COMBO_02_V2",
        name="ADAPTIVE_3_REGIME",
        description=(
            "RESEARCH COMBO_02 v2: regime-routed TREND_FOLLOWING / "
            "RANGE_MEAN_REVERSION / REVERSAL playbooks (LONG+SHORT). "
            "Uses existing stop/target/RR engines. Not COMBO_02 v1."
        ),
        require_bos=False,
        require_trend=False,
        require_htf_alignment=False,
        conditions=(
            "Regime router (TREND / RANGE / TRANSITION)",
            "Trend: 1H BOS + soft HTF (allow 4H neutral)",
            "Range: liquidity sweep + 15M structure confirm",
            "Reversal: sweep/CHoCH shift + 15M confirm",
            "Existing risk engines (stop/TP/RR)",
        ),
    ),
    # Research-only V2.1 variants — do NOT replace COMBO_02_V2 default.
    "COMBO_02_V2_1A": CombinationDefinition(
        combination_id="COMBO_02_V2_1A",
        name="ADAPTIVE_3_REGIME_V21A_CHOPPY_OFF",
        description=(
            "RESEARCH COMBO_02 v2.1-A: same as v2 but CHOPPY → WAIT "
            "(RANGE_MEAN_REVERSION disabled in CHOPPY only)."
        ),
        require_bos=False,
        require_trend=False,
        require_htf_alignment=False,
        conditions=(
            "Frozen v2 playbooks except CHOPPY range disabled",
            "CHOPPY → WAIT",
            "RANGE / HIGH_VOL_RANGE / LOW_VOL_COMPRESSION unchanged",
            "TREND / TRANSITION unchanged",
            "Existing risk engines (stop/TP/RR)",
        ),
    ),
    # Backtest-screen / API id (same VARIANT_A evaluator as COMBO_02_V2_1A).
    "COMBO_02_V2_1_A": CombinationDefinition(
        combination_id="COMBO_02_V2_1_A",
        name="ADAPTIVE_3_REGIME_V21A_CHOPPY_OFF",
        description=(
            "RESEARCH COMBO_02 V2.1-A (UI id): CHOPPY → WAIT. "
            "Same evaluator as COMBO_02_V2_1A; does not replace COMBO_02_V2."
        ),
        require_bos=False,
        require_trend=False,
        require_htf_alignment=False,
        conditions=(
            "Frozen v2 playbooks except CHOPPY range disabled",
            "CHOPPY → WAIT",
            "RANGE / HIGH_VOL_RANGE / LOW_VOL_COMPRESSION unchanged",
            "TREND / TRANSITION unchanged",
            "Existing risk engines (stop/TP/RR)",
        ),
    ),
    "COMBO_02_V2_1B": CombinationDefinition(
        combination_id="COMBO_02_V2_1B",
        name="ADAPTIVE_3_REGIME_V21B_CHOPPY_15M_REQ",
        description=(
            "RESEARCH COMBO_02 v2.1-B: same as v2 but CHOPPY range requires "
            "valid 15M structure confirmation (no optional pass)."
        ),
        require_bos=False,
        require_trend=False,
        require_htf_alignment=False,
        conditions=(
            "Frozen v2 playbooks except CHOPPY range 15M required",
            "CHOPPY range: sweep + close inside + required 15M confirm",
            "No 15M_UNAVAILABLE_OPTIONAL_PASS in CHOPPY",
            "TREND / TRANSITION / non-CHOPPY range unchanged",
            "Existing risk engines (stop/TP/RR)",
        ),
    ),
    "COMBO_03": CombinationDefinition(
        combination_id="COMBO_03",
        name="TREND_BOS_PULLBACK",
        description="BOS confirmed AND trend agrees AND pullback confirmed.",
        require_bos=True,
        require_trend=True,
        require_pullback=True,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "Pullback confirmed",
        ),
    ),
    # Research-only CHOPPY→transition experiment. Distinct from legacy COMBO_03
    # (TREND_BOS_PULLBACK). Does not alter COMBO_02 v1/v2.
    "COMBO_03_TRANSITION": CombinationDefinition(
        combination_id="COMBO_03_TRANSITION",
        name="CHOPPY_TRANSITION_BREAKOUT",
        description=(
            "RESEARCH COMBO_03_TRANSITION baseline: eligible regime "
            "(CHOPPY/RANGE/HIGH_VOLATILITY_RANGE/TRANSITION) + liquidity sweep + "
            "rejection + existing 1H BOS/CHoCH structure shift + mandatory "
            "genuine 15M confirmation. Existing risk engines only."
        ),
        require_bos=False,
        require_trend=False,
        require_htf_alignment=False,
        conditions=(
            "Eligible regime at setup time (existing classifier)",
            "Liquidity sweep + rejection_back_inside (existing sweep helper)",
            "Existing bullish/bearish 1H BOS or CHoCH structure shift",
            "Mandatory genuine 15M confirmation after 1H shift",
            "Existing stop/TP/RR engines (no alternate risk)",
        ),
    ),
    "COMBO_03_TRANSITION_B": CombinationDefinition(
        combination_id="COMBO_03_TRANSITION_B",
        name="CHOPPY_TRANSITION_BREAKOUT_NO_15M",
        description=(
            "RESEARCH comparison variant B: sweep + rejection + 1H structure "
            "shift without mandatory 15M confirmation. Not the primary baseline."
        ),
        require_bos=False,
        require_trend=False,
        require_htf_alignment=False,
        conditions=(
            "Eligible regime at setup time",
            "Sweep + rejection + existing 1H structure shift",
            "15M confirmation NOT mandatory (comparison only)",
            "Existing stop/TP/RR engines",
        ),
    ),
    "COMBO_03_TRANSITION_C": CombinationDefinition(
        combination_id="COMBO_03_TRANSITION_C",
        name="CHOPPY_TRANSITION_BREAKOUT_DISPLACEMENT",
        description=(
            "RESEARCH comparison variant C: baseline plus required displacement "
            "from the existing impulse engine on the structure-shift bar."
        ),
        require_bos=False,
        require_trend=False,
        require_htf_alignment=False,
        conditions=(
            "Eligible regime at setup time",
            "Sweep + rejection + existing 1H structure shift",
            "Mandatory genuine 15M confirmation",
            "Existing impulse/displacement PASS on structure-shift bar",
            "Existing stop/TP/RR engines",
        ),
    ),
    "COMBO_04": CombinationDefinition(
        combination_id="COMBO_04",
        name="TREND_BOS_IMPULSE_PULLBACK",
        description="BOS AND trend AND impulse AND pullback.",
        require_bos=True,
        require_trend=True,
        require_impulse=True,
        require_pullback=True,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "Impulse confirmed",
            "Pullback confirmed",
        ),
    ),
    # Research-only SHORT reverse of COMBO_02: same HTF hard gate plus impulse
    # and pullback required features. Not v1 production / not paper-eligible.
    "COMBO_04_HTF": CombinationDefinition(
        combination_id="COMBO_04_HTF",
        name="TREND_BOS_IMPULSE_PULLBACK_HTF",
        description=(
            "Research-only reverse of COMBO_02 long: BOS + setup-TF trend + "
            "4h+1h HTF alignment + impulse + pullback. Used for SHORT "
            "Strategy Backtest. Not v1 production."
        ),
        require_bos=True,
        require_trend=True,
        require_impulse=True,
        require_pullback=True,
        require_htf_alignment=True,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "4h+1h HTF structure aligned (hard gate)",
            "Impulse confirmed",
            "Pullback confirmed",
        ),
    ),
    "COMBO_05": CombinationDefinition(
        combination_id="COMBO_05",
        name="TREND_BOS_IMPULSE_PULLBACK_RVOL",
        description="TREND_BOS_IMPULSE_PULLBACK AND RVOL confirmation.",
        require_bos=True,
        require_trend=True,
        require_impulse=True,
        require_pullback=True,
        require_rvol=True,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "Impulse confirmed",
            "Pullback confirmed",
            "RVOL confirmation",
        ),
    ),
    "COMBO_06": CombinationDefinition(
        combination_id="COMBO_06",
        name="TREND_BOS_PULLBACK_SD",
        description="BOS AND trend AND pullback AND Supply/Demand confluence.",
        require_bos=True,
        require_trend=True,
        require_pullback=True,
        require_sd=True,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "Pullback confirmed",
            "Supply/Demand confluence",
        ),
    ),
    "COMBO_07": CombinationDefinition(
        combination_id="COMBO_07",
        name="TREND_BOS_IMPULSE_PULLBACK_RVOL_SD",
        description="All prior gates including impulse, RVOL, and S/D.",
        require_bos=True,
        require_trend=True,
        require_impulse=True,
        require_pullback=True,
        require_rvol=True,
        require_sd=True,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "Impulse confirmed",
            "Pullback confirmed",
            "RVOL confirmation",
            "Supply/Demand confluence",
        ),
    ),
    "COMBO_08": CombinationDefinition(
        combination_id="COMBO_08",
        name="TREND_BOS_IMPULSE_PULLBACK_RVOL_SD_RR",
        description="All gates AND R:R >= configured minimum.",
        require_bos=True,
        require_trend=True,
        require_impulse=True,
        require_pullback=True,
        require_rvol=True,
        require_sd=True,
        require_rr=True,
        conditions=(
            "BOS confirmed",
            "Trend agrees with BOS direction",
            "Impulse confirmed",
            "Pullback confirmed",
            "RVOL confirmation",
            "Supply/Demand confluence",
            "R:R >= configured minimum",
        ),
    ),
}


def get_combination(combination_id: str) -> CombinationDefinition | None:
    key = combination_id.upper().strip()
    if key in COMBINATIONS:
        return COMBINATIONS[key]
    for combo in COMBINATIONS.values():
        if combo.name == key or combo.name == combination_id:
            return combo
    return None


def list_combinations() -> list[dict[str, Any]]:
    return [c.to_dict() for c in COMBINATIONS.values()]
