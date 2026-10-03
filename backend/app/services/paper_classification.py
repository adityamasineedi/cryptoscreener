"""Explicit paper-trade / alert classification — never infer v1 from titles.

Frozen COMBO_02 v1 watcher trades vs legacy research 15m Path A/B.
Presentation and Telegram/monitor gates only; does not alter strategy logic.
"""

from __future__ import annotations

from typing import Any

# Frozen v1 watcher identity
STRATEGY_COMBO_02_V1 = "COMBO_02_V1"
SOURCE_V1_PAPER_WATCHER = "V1_PAPER_WATCHER"
COMBO_ID_V1 = "COMBO_02"
COMBO_VERSION_V1 = "v1-combo02-long-htf"
PATH_V1 = "A"
TIMEFRAME_V1 = "1h"
V1_SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT"})

# Legacy / research
STRATEGY_RESEARCH_15M = "RESEARCH_15M"
STRATEGY_EXPERIMENTAL_PATH_B = "EXPERIMENTAL_PATH_B"
SOURCE_LEGACY_SETUP_SIGNAL = "LEGACY_SETUP_SIGNAL"
PATH_LEGACY = "LEGACY"
PATH_B = "B"

# UI badge categories
BADGE_V1_VERIFIED = "V1_VERIFIED"
BADGE_RESEARCH_15M = "RESEARCH_15M"
BADGE_STRUCTURE = "STRUCTURE"
BADGE_LIQUIDATIONS = "LIQUIDATIONS"
BADGE_EXPERIMENTAL = "EXPERIMENTAL"


def _snip(pos_or_alert: dict[str, Any]) -> dict[str, Any]:
    payload = pos_or_alert.get("payload")
    if isinstance(payload, dict):
        raw = payload.get("signal_snippet") or {}
        if isinstance(raw, dict) and raw:
            return raw
        # Classification may also live on the payload root (alert fan-out).
        return payload
    raw = pos_or_alert.get("signal_snippet") or {}
    return raw if isinstance(raw, dict) else {}


def v1_gate_metadata_complete(snip: dict[str, Any], *, symbol: str, timeframe: str) -> bool:
    """All fields required for telegram_eligible=True on a v1 watcher trade."""
    if str(symbol or "").upper() not in V1_SYMBOLS:
        return False
    if str(timeframe or "").lower() != TIMEFRAME_V1:
        return False
    if str(snip.get("strategy_id") or "") != STRATEGY_COMBO_02_V1:
        return False
    if str(snip.get("source") or "") != SOURCE_V1_PAPER_WATCHER:
        return False
    if str(snip.get("combo_id") or "").upper() != COMBO_ID_V1:
        return False
    if str(snip.get("combo_version") or "").lower() != COMBO_VERSION_V1:
        return False
    path = str(snip.get("path") or "").upper().replace("PATH_", "")
    if path != PATH_V1:
        return False
    if str(snip.get("htf_alignment") or "").upper() != "HTF_ALIGNED":
        return False
    if str(snip.get("trend_1h") or "").upper() != "BULLISH":
        return False
    if str(snip.get("trend_4h") or "").upper() != "BULLISH":
        return False
    return True


def classify_v1_watcher(
    *,
    symbol: str,
    timeframe: str,
    combo_id: str,
    combo_version: str,
    path: str,
    htf_alignment: str | None,
    trend_1h: str | None = None,
    trend_4h: str | None = None,
    v1_tier: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build classification fields for V1PaperWatcher opens."""
    snip: dict[str, Any] = {
        "strategy_id": STRATEGY_COMBO_02_V1,
        "source": SOURCE_V1_PAPER_WATCHER,
        "combo_id": combo_id,
        "combo_version": combo_version,
        "path": path,
        "symbol": str(symbol or "").upper(),
        "timeframe": str(timeframe or TIMEFRAME_V1).lower(),
        "htf_alignment": str(htf_alignment or "").upper() or None,
        "trend_1h": str(trend_1h or "").upper() or None,
        "trend_4h": str(trend_4h or "").upper() or None,
        "v1_tier": v1_tier,
    }
    if extra:
        snip.update(extra)
    snip["telegram_eligible"] = v1_gate_metadata_complete(
        snip, symbol=snip["symbol"], timeframe=snip["timeframe"]
    )
    return snip


def classify_legacy_setup(
    *,
    symbol: str,
    timeframe: str,
    path_b: bool,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build classification for generic setup-signal paper opens (never v1)."""
    sym = str(symbol or "").upper()
    tf = str(timeframe or "15m").lower()
    if path_b:
        strategy_id = STRATEGY_EXPERIMENTAL_PATH_B
        path = PATH_B
    else:
        strategy_id = STRATEGY_RESEARCH_15M
        path = PATH_LEGACY
    snip: dict[str, Any] = {
        "strategy_id": strategy_id,
        "source": SOURCE_LEGACY_SETUP_SIGNAL,
        "combo_id": None,
        "combo_version": None,
        "path": path,
        "symbol": sym,
        "timeframe": tf,
        "telegram_eligible": False,
    }
    if extra:
        # Preserve research metadata but never allow v1 identity overrides.
        for k, v in extra.items():
            if k in (
                "strategy_id",
                "source",
                "combo_id",
                "combo_version",
                "path",
                "telegram_eligible",
            ):
                continue
            snip[k] = v
    return snip


def classification_fields_from_position(pos: dict[str, Any]) -> dict[str, Any]:
    """Extract/normalize classification for alert top-level + monitor filters.

    Fail-closed: missing identity → not telegram eligible, not v1.
    Legacy rows without fields are treated as RESEARCH_15M (never promoted to v1).
    """
    snip = _snip(pos)
    symbol = str(
        pos.get("symbol") or snip.get("symbol") or ""
    ).upper()
    timeframe = str(
        pos.get("timeframe") or snip.get("timeframe") or ""
    ).lower()

    strategy_id = snip.get("strategy_id")
    source = snip.get("source")

    # Normalize historical watcher rows that only had source="v1_paper_watcher"
    if not strategy_id and str(source or "").lower() in (
        "v1_paper_watcher",
        SOURCE_V1_PAPER_WATCHER.lower(),
    ):
        strategy_id = STRATEGY_COMBO_02_V1
        source = SOURCE_V1_PAPER_WATCHER

    if not strategy_id or not source:
        # Never infer v1 from combo_version/title. Default to research legacy.
        strategy_id = STRATEGY_RESEARCH_15M
        source = SOURCE_LEGACY_SETUP_SIGNAL
        combo_id = None
        combo_version = None
        path = PATH_LEGACY
        telegram_eligible = False
    else:
        combo_id = snip.get("combo_id")
        combo_version = snip.get("combo_version")
        path = snip.get("path")
        if path is not None:
            path = str(path).upper().replace("PATH_", "")
            if path == "A" and strategy_id != STRATEGY_COMBO_02_V1:
                # Legacy Path A must not look like v1 Path A
                if strategy_id == STRATEGY_RESEARCH_15M:
                    path = PATH_LEGACY
        merged = {
            **snip,
            "strategy_id": strategy_id,
            "source": source,
            "combo_id": combo_id,
            "combo_version": combo_version,
            "path": path,
        }
        telegram_eligible = bool(snip.get("telegram_eligible")) and v1_gate_metadata_complete(
            merged, symbol=symbol, timeframe=timeframe
        )

    return {
        "strategy_id": strategy_id,
        "source": source,
        "combo_id": combo_id,
        "combo_version": combo_version,
        "path": path,
        "symbol": symbol,
        "timeframe": timeframe,
        "telegram_eligible": bool(telegram_eligible),
        "htf_alignment": snip.get("htf_alignment"),
        "v1_tier": snip.get("v1_tier"),
    }


def is_v1_classified(fields: dict[str, Any]) -> bool:
    return (
        str(fields.get("strategy_id") or "") == STRATEGY_COMBO_02_V1
        and str(fields.get("source") or "") == SOURCE_V1_PAPER_WATCHER
        and str(fields.get("combo_id") or "").upper() == COMBO_ID_V1
        and str(fields.get("combo_version") or "").lower() == COMBO_VERSION_V1
        and str(fields.get("path") or "").upper().replace("PATH_", "") == PATH_V1
        and str(fields.get("timeframe") or "").lower() == TIMEFRAME_V1
    )


def is_legacy_research(fields: dict[str, Any]) -> bool:
    sid = str(fields.get("strategy_id") or "")
    src = str(fields.get("source") or "")
    if src == SOURCE_LEGACY_SETUP_SIGNAL:
        return True
    if sid in (STRATEGY_RESEARCH_15M, STRATEGY_EXPERIMENTAL_PATH_B):
        return True
    return False


def alert_badge_category(alert: dict[str, Any]) -> str:
    """Map an alert to a UI badge category."""
    alert_type = str(alert.get("type") or "").upper()
    if alert_type in ("BOS", "CHOCH", "SETUP_STATUS", "MARKET_SIGNAL"):
        return BADGE_STRUCTURE
    if alert_type == "LIQ_SPIKE":
        return BADGE_LIQUIDATIONS
    if alert_type in ("PAPER_ENTRY", "PAPER_EXIT"):
        fields = classification_fields_from_position(alert)
        if is_v1_classified(fields):
            return BADGE_V1_VERIFIED
        if str(fields.get("strategy_id") or "") == STRATEGY_EXPERIMENTAL_PATH_B or str(
            fields.get("path") or ""
        ).upper().replace("PATH_", "") == "B":
            return BADGE_EXPERIMENTAL
        return BADGE_RESEARCH_15M
    return BADGE_STRUCTURE


def alert_subtitle(alert: dict[str, Any]) -> str:
    """Human subtitle for alert rows."""
    cat = alert_badge_category(alert)
    fields = classification_fields_from_position(alert)
    if cat == BADGE_V1_VERIFIED:
        tier = str(fields.get("v1_tier") or "").upper() or "—"
        htf = str(fields.get("htf_alignment") or "HTF_ALIGNED").replace("_", " ")
        return f"COMBO_02 v1 • 1h • {htf} • {tier}"
    if cat == BADGE_RESEARCH_15M:
        tf = fields.get("timeframe") or alert.get("timeframe") or "15m"
        return f"RESEARCH ONLY • {tf} • Not Telegram eligible"
    if cat == BADGE_EXPERIMENTAL:
        return "EXPERIMENTAL • Path B • Not Telegram eligible"
    if cat == BADGE_LIQUIDATIONS:
        return "Liquidation spike"
    return "Structure / setup"
