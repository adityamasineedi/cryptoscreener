"""Fee / execution sensitivity for SHORT research diagnostics."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.trade_fees import (
    DEFAULT_MAKER_FEE,
    DEFAULT_TAKER_FEE,
    enrich_trade_execution,
)


def fee_ratio(gross_pnl: float | None, fees: float | None) -> float | None:
    if gross_pnl is None or fees is None:
        return None
    ag = abs(float(gross_pnl))
    if ag <= 1e-12:
        return None
    return abs(float(fees)) / ag


def reprice_trade_fees(
    trade: Mapping[str, Any],
    *,
    risk_usd: float,
    entry_type: str,
    taker_fee: float = DEFAULT_TAKER_FEE,
    maker_fee: float = DEFAULT_MAKER_FEE,
    exit_fee: float | None = None,
    slippage_bps: float = 0.0,
) -> dict[str, Any]:
    """Recompute fees/PnL under an alternate execution assumption (research-only)."""
    row = dict(trade)
    entry = float(row.get("entry_price") or 0)
    exit_px = row.get("exit_price")
    direction = str(row.get("direction") or "SHORT").upper()
    slip = float(slippage_bps) / 10_000.0
    if slip and entry > 0:
        # Conservative: SHORT entry worse (lower), exit worse (higher)
        if direction == "SHORT":
            entry = entry * (1.0 - slip)
            if exit_px is not None:
                exit_px = float(exit_px) * (1.0 + slip)
        else:
            entry = entry * (1.0 + slip)
            if exit_px is not None:
                exit_px = float(exit_px) * (1.0 - slip)
    row["entry_price"] = entry
    row["exit_price"] = exit_px
    row["entry_type"] = entry_type
    row["condition_snapshot"] = {
        **(dict(row.get("condition_snapshot") or {})),
        "entry_type": entry_type,
    }
    enriched = enrich_trade_execution(
        row,
        risk_usd=risk_usd,
        taker_fee=taker_fee,
        maker_fee=maker_fee,
        exit_fee=exit_fee,
    )
    return {
        "entry_type": entry_type,
        "taker_fee": taker_fee,
        "maker_fee": maker_fee,
        "slippage_bps": slippage_bps,
        "gross_pnl": enriched.get("gross_pnl"),
        "fees": enriched.get("total_fee"),
        "entry_fee": enriched.get("entry_fee"),
        "exit_fee": enriched.get("exit_fee"),
        "net_pnl": enriched.get("net_pnl"),
        "r_net": enriched.get("r_net"),
        "fees_over_abs_gross": fee_ratio(enriched.get("gross_pnl"), enriched.get("total_fee")),
        "entry_fee_type": enriched.get("entry_fee_type"),
        "exit_fee_type": enriched.get("exit_fee_type"),
    }


def fee_sensitivity_matrix(
    trades: Sequence[Mapping[str, Any]],
    *,
    risk_usd: float = 20.0,
) -> dict[str, Any]:
    scenarios = {
        "live_model": {"entry_type": None, "taker_fee": DEFAULT_TAKER_FEE, "maker_fee": DEFAULT_MAKER_FEE, "slippage_bps": 0.0},
        "market_entry_taker": {"entry_type": "MARKET", "taker_fee": DEFAULT_TAKER_FEE, "maker_fee": DEFAULT_MAKER_FEE, "slippage_bps": 0.0},
        "limit_retest_maker": {"entry_type": "LIMIT_RETEST", "taker_fee": DEFAULT_TAKER_FEE, "maker_fee": DEFAULT_MAKER_FEE, "slippage_bps": 0.0},
        "market_exit_taker": {"entry_type": None, "taker_fee": DEFAULT_TAKER_FEE, "maker_fee": DEFAULT_MAKER_FEE, "slippage_bps": 0.0},
        "conservative_slippage_2bps": {"entry_type": None, "taker_fee": DEFAULT_TAKER_FEE, "maker_fee": DEFAULT_MAKER_FEE, "slippage_bps": 2.0},
        "zero_fees": {"entry_type": None, "taker_fee": 0.0, "maker_fee": 0.0, "slippage_bps": 0.0},
    }
    out: dict[str, Any] = {}
    for name, cfg in scenarios.items():
        gross = 0.0
        fees = 0.0
        net = 0.0
        rs: list[float] = []
        for t in trades:
            # Prefer blotter-accurate stored values for the live-model baseline.
            if (
                name == "live_model"
                and t.get("gross_pnl") is not None
                and t.get("net_pnl") is not None
                and t.get("fees") is not None
            ):
                g = float(t["gross_pnl"])
                f = float(t["fees"])
                n = float(t["net_pnl"])
                r = t.get("R")
                if r is None:
                    r = t.get("r_net")
                gross += g
                fees += f
                net += n
                if r is not None:
                    rs.append(float(r))
                continue
            et = cfg["entry_type"] or str(
                t.get("entry_type")
                or (t.get("condition_snapshot") or {}).get("entry_type")
                or "MARKET"
            )
            row = dict(t)
            if row.get("exit_price") is None:
                oc = str(row.get("outcome") or "").upper()
                if oc in ("SL", "STOP"):
                    row["exit_price"] = row.get("stop_price")
                elif oc.startswith("TP"):
                    row["exit_price"] = row.get("TP1") or row.get("tp1")
            priced = reprice_trade_fees(
                row,
                risk_usd=risk_usd,
                entry_type=str(et),
                taker_fee=float(cfg["taker_fee"]),
                maker_fee=float(cfg["maker_fee"]),
                slippage_bps=float(cfg["slippage_bps"]),
            )
            if priced.get("gross_pnl") is not None:
                gross += float(priced["gross_pnl"])
            if priced.get("fees") is not None:
                fees += float(priced["fees"])
            if priced.get("net_pnl") is not None:
                net += float(priced["net_pnl"])
            if priced.get("r_net") is not None:
                rs.append(float(priced["r_net"]))
        wins = sum(1 for r in rs if r > 0)
        gains = sum(r for r in rs if r > 0)
        losses = sum(abs(r) for r in rs if r < 0)
        out[name] = {
            "trade_count": len(trades),
            "gross_pnl": gross,
            "fees": fees,
            "net_pnl": net,
            "average_net_r": (sum(rs) / len(rs)) if rs else None,
            "win_rate": (wins / len(rs)) if rs else None,
            "profit_factor": (gains / losses) if losses > 0 else (float("inf") if gains > 0 else None),
            "positive_only_with_zero_fees": name == "zero_fees" and net > 0,
        }
    live_net = float((out.get("live_model") or {}).get("net_pnl") or 0)
    zero_net = float((out.get("zero_fees") or {}).get("net_pnl") or 0)
    out["interpretation"] = {
        "gross_negative": float((out.get("live_model") or {}).get("gross_pnl") or 0) < 0,
        "net_negative": live_net < 0,
        "fees_flip_sign": zero_net > 0 and live_net <= 0,
        "acceptable_for_paper": live_net > 0,  # still not paper-eligible in this phase
        "note": (
            "A strategy that is positive only with zero fees is not acceptable "
            "for paper validation."
        ),
    }
    return out
