"""Reproducible COMBO_02 candidate selector (research only).

Selects liquid futures screener coins using persisted rules — not the
visual Top-100 screen alone. Never modifies v1_production or Telegram.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.research.combo02_candidate_thresholds import (
    DEFAULT_SELECTOR,
    DISCLAIMER,
    CandidateSelectorConfig,
)
from app.research.v1_production import V1_SYMBOLS
from app.services.database import db_manager

_STABLE_BASES = frozenset(
    {
        "USDT",
        "USDC",
        "BUSD",
        "TUSD",
        "USDP",
        "DAI",
        "FDUSD",
        "USDD",
        "GUSD",
        "USDE",
        "PYUSD",
        "EUR",
        "GBP",
        "AUD",
        "BRL",
    }
)
_LEVERAGE_RE = re.compile(
    r"(UP|DOWN|BULL|BEAR|3L|3S|5L|5S|2L|2S)$", re.IGNORECASE
)
_NONSTANDARD_RE = re.compile(r"[^A-Z0-9]")


def base_asset(symbol: str, quote_asset: str = "USDT") -> str:
    sym = symbol.upper().strip()
    q = quote_asset.upper()
    if sym.endswith(q):
        return sym[: -len(q)]
    return sym


def exclusion_reason(
    symbol: str,
    *,
    config: CandidateSelectorConfig = DEFAULT_SELECTOR,
    status: str | None = None,
    contract_type: str | None = None,
    quote_asset: str | None = None,
    market_type: str | None = None,
) -> str | None:
    """Return a machine reason if symbol must be excluded, else None."""
    sym = symbol.upper().strip()
    quote = (quote_asset or config.quote_asset).upper()
    if not sym.endswith(quote):
        return "non_usdt_quote" if quote == "USDT" else "wrong_quote_asset"

    if config.exclude_inactive_or_delisted:
        st = (status or "TRADING").upper()
        if st not in {"TRADING", "ACTIVE", ""}:
            return "inactive"

    if config.market_type.lower() in {"perpetual", "perp", "futures_perp"}:
        mt = (market_type or "").lower()
        ct = (contract_type or "").upper()
        if mt and mt not in {"futures_perp", "perpetual", "perp"}:
            return "not_perpetual"
        if ct and ct not in {"PERPETUAL", ""}:
            return "not_perpetual"

    base = base_asset(sym, quote)
    if config.exclude_stablecoins:
        if base in _STABLE_BASES or base.endswith("USD"):
            return "stablecoin"

    if config.exclude_leveraged_tokens:
        if _LEVERAGE_RE.search(base) or "BULL" in base or "BEAR" in base:
            return "leveraged_token"

    if config.exclude_nonstandard_symbols:
        if _NONSTANDARD_RE.search(base) or len(base) > 12 or not base:
            return "nonstandard_symbol"

    if config.exclude_v1_universe and sym in V1_SYMBOLS:
        return "v1_universe_reserved"

    return None


async def _load_symbol_meta() -> dict[str, dict[str, Any]]:
    """Futures metadata from market_store and/or DB symbols table."""
    meta: dict[str, dict[str, Any]] = {}
    try:
        from app.services.market_store import market_store

        for info in market_store.list_symbols(market_type="futures_perp"):
            sym = str(info.symbol).upper()
            meta[sym] = {
                "symbol": sym,
                "status": getattr(info, "status", "TRADING"),
                "contract_type": getattr(info, "contract_type", None),
                "quote_asset": getattr(info, "quote_asset", "USDT"),
                "market_type": getattr(info, "market_type", "futures_perp"),
                "base_asset": getattr(info, "base_asset", None),
            }
    except Exception:  # noqa: BLE001
        pass

    if meta:
        return meta
    if not db_manager.enabled or db_manager.engine is None:
        return meta

    from sqlalchemy import text

    sql = text(
        """
        SELECT symbol, status, contract_type, quote_asset, market_type, base_asset
        FROM symbols
        WHERE quote_asset = 'USDT'
          AND (market_type IN ('futures_perp', 'perpetual') OR contract_type = 'PERPETUAL')
        """
    )
    try:
        async with db_manager.engine.connect() as conn:
            result = await conn.execute(sql)
            for row in result.mappings():
                sym = str(row["symbol"]).upper()
                meta[sym] = {
                    "symbol": sym,
                    "status": row.get("status") or "TRADING",
                    "contract_type": row.get("contract_type"),
                    "quote_asset": row.get("quote_asset") or "USDT",
                    "market_type": row.get("market_type") or "futures_perp",
                    "base_asset": row.get("base_asset"),
                }
    except Exception:  # noqa: BLE001
        pass

    if meta:
        return meta

    # Last resort: distinct USDT symbols present in OHLCV (status assumed TRADING).
    try:
        sql2 = text(
            """
            SELECT DISTINCT symbol
            FROM ohlcv
            WHERE timeframe = '1h' AND symbol LIKE '%USDT'
            """
        )
        async with db_manager.engine.connect() as conn:
            result = await conn.execute(sql2)
            for row in result.mappings():
                sym = str(row["symbol"]).upper()
                meta[sym] = {
                    "symbol": sym,
                    "status": "TRADING",
                    "contract_type": "PERPETUAL",
                    "quote_asset": "USDT",
                    "market_type": "futures_perp",
                    "base_asset": sym[:-4] if sym.endswith("USDT") else sym,
                }
    except Exception:  # noqa: BLE001
        pass
    return meta


async def _volume_by_symbol() -> dict[str, float]:
    """Known 24h quote volumes only — never invent missing values.

    Prefer live ticker cache; fall back to sum of last ~24 1h `quote_volume`
    bars from OHLCV when tickers are unavailable (still measured, not fabricated).
    """
    volumes: dict[str, float] = {}
    try:
        from app.services.market_store import market_store

        for sym, t in (market_store.tickers or {}).items():
            qv = getattr(t, "quote_volume_24h", None)
            if qv is None:
                continue
            try:
                qvf = float(qv)
            except (TypeError, ValueError):
                continue
            if qvf > 0:
                volumes[str(sym).upper()] = qvf
    except Exception:  # noqa: BLE001
        pass
    if volumes:
        return volumes

    if not db_manager.enabled or db_manager.engine is None:
        return volumes

    from sqlalchemy import text

    # Measured liquidity proxy: sum of quote_volume over the latest 24 1h bars.
    sql = text(
        """
        WITH ranked AS (
            SELECT symbol, quote_volume,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY time DESC) AS rn
            FROM ohlcv
            WHERE timeframe = '1h'
              AND symbol LIKE '%USDT'
              AND quote_volume IS NOT NULL
        )
        SELECT symbol, SUM(quote_volume) AS qv_24h
        FROM ranked
        WHERE rn <= 24
        GROUP BY symbol
        HAVING SUM(quote_volume) > 0
        ORDER BY qv_24h DESC
        """
    )
    try:
        async with db_manager.engine.connect() as conn:
            result = await conn.execute(sql)
            for row in result.mappings():
                try:
                    qvf = float(row["qv_24h"] or 0)
                except (TypeError, ValueError):
                    continue
                if qvf > 0:
                    volumes[str(row["symbol"]).upper()] = qvf
    except Exception:  # noqa: BLE001
        pass
    return volumes


async def coverage_1h(symbol: str) -> dict[str, Any]:
    """OHLCV coverage from the canonical `ohlcv` table (time column)."""
    from sqlalchemy import text

    if not db_manager.enabled or db_manager.engine is None:
        return {}
    sql = text(
        """
        SELECT COUNT(*) AS bars,
               MIN(time) AS first_ts,
               MAX(time) AS last_ts
        FROM ohlcv
        WHERE symbol = :sym AND timeframe = '1h'
        """
    )
    async with db_manager.engine.connect() as conn:
        row = (await conn.execute(sql, {"sym": symbol})).mappings().first()
    if not row:
        return {}
    first = row["first_ts"]
    last = row["last_ts"]
    bars = int(row["bars"] or 0)
    days = None
    completeness = None
    if first is not None and last is not None:
        days = (last - first).total_seconds() / 86400.0
        if days > 0:
            expected = days * 24.0
            completeness = bars / expected if expected else None
    return {
        "bars_1h": bars,
        "ohlcv_start_utc": first.isoformat() if first else None,
        "ohlcv_end_utc": last.isoformat() if last else None,
        "history_days": round(days, 2) if days is not None else None,
        "ohlcv_completeness": round(completeness, 4) if completeness is not None else None,
    }


async def select_candidates(
    *,
    config: CandidateSelectorConfig | None = None,
    include_symbols: list[str] | None = None,
    screen_timestamp_utc: str | None = None,
) -> dict[str, Any]:
    """Build an immutable candidate manifest payload."""
    cfg = config or DEFAULT_SELECTOR
    generated_at = datetime.now(timezone.utc).isoformat()
    screen_ts = screen_timestamp_utc or generated_at
    preferred = {s.upper() for s in (include_symbols or [])}

    meta = await _load_symbol_meta()
    volumes = await _volume_by_symbol()

    # Rank by known volume only. Symbols without volume are considered later
    # only if explicitly preferred — otherwise excluded (no invented ranks).
    ranked_syms = sorted(volumes.keys(), key=lambda s: volumes[s], reverse=True)
    for sym in sorted(meta.keys()):
        if sym not in volumes and sym not in ranked_syms:
            ranked_syms.append(sym)

    excluded: list[dict[str, Any]] = []
    symbols_out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for sym in ranked_syms:
        if sym in seen:
            continue
        seen.add(sym)
        info = meta.get(sym, {})
        reason = exclusion_reason(
            sym,
            config=cfg,
            status=info.get("status"),
            contract_type=info.get("contract_type"),
            quote_asset=info.get("quote_asset") or cfg.quote_asset,
            market_type=info.get("market_type"),
        )
        if reason:
            excluded.append({"symbol": sym, "reason": reason})
            continue

        qv = volumes.get(sym)
        if qv is None and sym not in preferred:
            excluded.append({"symbol": sym, "reason": "volume_unavailable"})
            continue
        if (
            qv is not None
            and float(qv) < cfg.min_avg_24h_quote_volume_usd
            and sym not in preferred
        ):
            excluded.append(
                {
                    "symbol": sym,
                    "reason": "low_liquidity",
                    "avg_24h_quote_volume_usd": qv,
                }
            )
            continue

        cov = await coverage_1h(sym)
        days = cov.get("history_days")
        if days is None and sym not in preferred:
            excluded.append({"symbol": sym, "reason": "insufficient_history"})
            continue
        if (
            days is not None
            and float(days) < cfg.min_history_days
            and sym not in preferred
        ):
            excluded.append(
                {
                    "symbol": sym,
                    "reason": "insufficient_history",
                    "history_days": days,
                }
            )
            continue

        comp = cov.get("ohlcv_completeness")
        if (
            comp is not None
            and float(comp) < cfg.min_ohlcv_completeness
            and sym not in preferred
        ):
            excluded.append(
                {
                    "symbol": sym,
                    "reason": "low_completeness",
                    "ohlcv_completeness": comp,
                }
            )
            continue
        if (cov.get("bars_1h") or 0) < 100 and sym not in preferred:
            excluded.append({"symbol": sym, "reason": "insufficient_history", **cov})
            continue

        symbols_out.append(
            {
                "symbol": sym,
                "rank": len(symbols_out) + 1,
                "avg_24h_quote_volume_usd": qv if qv is not None else 0,
                "ohlcv_start_utc": cov.get("ohlcv_start_utc"),
                "ohlcv_end_utc": cov.get("ohlcv_end_utc"),
                "history_days": cov.get("history_days") or 0,
                "ohlcv_completeness": cov.get("ohlcv_completeness") or 0.0,
                "inclusion_reason": (
                    "preferred_include"
                    if sym in preferred
                    else (
                        f"top_liquidity_rank_with_history>="
                        f"{cfg.min_history_days}d_completeness>="
                        f"{cfg.min_ohlcv_completeness}"
                    )
                ),
            }
        )
        if len(symbols_out) >= cfg.top_n:
            break

    return {
        "generated_at_utc": generated_at,
        "selector_version": cfg.selector_version,
        "criteria": cfg.to_dict(),
        "screen_timestamp_utc": screen_ts,
        "symbols": symbols_out,
        "excluded": excluded,
        "disclaimer": DISCLAIMER,
        # Back-compat aliases consumed by older runner snippets
        "selected_at_utc": generated_at,
        "selection_rules": cfg.to_dict(),
        "candidates": symbols_out,
        "candidate_count": len(symbols_out),
        "excluded_count": len(excluded),
    }


def manifest_symbols(manifest: dict[str, Any]) -> list[str]:
    rows = manifest.get("symbols") or manifest.get("candidates") or []
    return [str(r["symbol"]).upper() for r in rows if r.get("symbol")]
