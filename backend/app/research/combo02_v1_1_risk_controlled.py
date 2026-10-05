"""COMBO_02_V1_1_RISK_CONTROLLED — portfolio risk overlay on frozen COMBO_02 signals.

Signal logic (swing / BOS / HTF / stop / TP) is reused from COMBO_02 / COMBO_02_V1
unchanged. This module only applies entry-blocking risk controls.

COMBO_02_V1 remains archived (OOS_FAIL on 2025-07-01→2026-09-30). Do not mutate it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

STRATEGY_ID = "COMBO_02_V1_1_RISK_CONTROLLED"
COMBO_ID = "COMBO_02"  # same signal combo
PARENT_STRATEGY_ID = "COMBO_02_V1"
PARENT_COMBO_VERSION = "v1-combo02-long-htf"
VARIANT_VERSION = "v1.1-combo02-long-htf-risk-controlled"

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
SETUP_TF = "1h"

# Frozen evidence-era per-symbol risk (do not flatten).
# Matches historical v1_production profile: BTC core 1.5%, ETH/SOL secondary 0.5%.
RISK_PERCENT_BY_SYMBOL: dict[str, float] = {
    "BTCUSDT": 0.015,
    "ETHUSDT": 0.005,
    "SOLUSDT": 0.005,
}

CLUSTER_HEAT_CAP = 0.04  # 4% equity
MAX_CONCURRENT_CLUSTER = 2
DAILY_LOSS_HALT_R = 2.0
CONSECUTIVE_LOSS_HALT = 3
CONSECUTIVE_LOSS_PAUSE = timedelta(hours=24)
STRATEGY_DD_HALT_R = 6.0
SYMBOL_DD_HALT_R = 4.0

# Formal OOS for v1.1 — disjoint from base / OOS-dev / failed v1 OOS.
# Do not evaluate for acceptance until end time has passed.
OOS_START = "2026-10-06"
OOS_END = "2027-03-31"
OOS_END_EXCLUSIVE = "2027-04-01"  # end inclusive 2027-03-31 23:59:59 UTC

# Operational smoke only — NOT acceptance evidence.
SMOKE_OOS_START = "2026-10-01"
SMOKE_OOS_END = "2026-10-05"

# Diagnostic-only (NOT acceptance): failed COMBO_02_V1 OOS window.
DIAGNOSTIC_FAILED_V1_OOS_START = "2025-07-01"
DIAGNOSTIC_FAILED_V1_OOS_END = "2026-09-30"

REJECT_CLUSTER_HEAT = "CLUSTER_HEAT_EXCEEDED"
REJECT_MAX_CONCURRENT = "MAX_CONCURRENT_EXCEEDED"
REJECT_DAILY_LOSS = "DAILY_LOSS_HALT"
REJECT_SYMBOL_STREAK = "SYMBOL_CONSECUTIVE_LOSS_PAUSE"
REJECT_STRATEGY_DD = "STRATEGY_DRAWDOWN_HALT"
REJECT_SYMBOL_DD = "SYMBOL_DRAWDOWN_HALT"

# Sample thresholds for formal OOS classification.
MIN_SAMPLE_READY_FOR_PAPER = 30
MIN_SAMPLE_PASS_WITH_REVIEW = 15
MIN_SAMPLE_PER_SYMBOL_CLAIM = 20
MAX_DD_R = 6.0
MAX_LOSE_STREAK = 8
MIN_COMPLETENESS = 0.99


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def risk_percent_for(symbol: str) -> float:
    return float(RISK_PERCENT_BY_SYMBOL[str(symbol or "").upper()])


def risk_usd_for(symbol: str, principal_usd: float = 1000.0) -> float:
    return float(principal_usd) * risk_percent_for(symbol)


@dataclass
class OpenSlot:
    symbol: str
    opened_at: datetime
    risk_percent: float
    entry_key: str


@dataclass
class RiskControlledState:
    """Mutable book state for chronological portfolio simulation / paper gates."""

    open: dict[str, OpenSlot] = field(default_factory=dict)
    day_key: str | None = None
    day_pnl_r: float = 0.0
    consecutive_losses: dict[str, int] = field(default_factory=dict)
    symbol_pause_until: dict[str, datetime] = field(default_factory=dict)
    strategy_equity_r: float = 0.0
    strategy_peak_r: float = 0.0
    symbol_equity_r: dict[str, float] = field(default_factory=dict)
    symbol_peak_r: dict[str, float] = field(default_factory=dict)
    reject_counts: dict[str, int] = field(default_factory=dict)
    rejects: list[dict[str, Any]] = field(default_factory=list)

    def _roll_day(self, when: datetime) -> None:
        key = when.astimezone(timezone.utc).strftime("%Y-%m-%d")
        if self.day_key != key:
            self.day_key = key
            self.day_pnl_r = 0.0

    def open_risk_pct(self) -> float:
        return sum(float(s.risk_percent) for s in self.open.values())

    def try_open(
        self,
        *,
        symbol: str,
        signal_time: datetime | str,
        risk_percent: float | None = None,
        entry_key: str = "",
    ) -> tuple[bool, str]:
        """Return (ok, reason). Blocks new entries only — never force-closes."""
        sym = str(symbol or "").upper()
        when = _parse_ts(signal_time)
        if when is None:
            reason = "INVALID_SIGNAL_TIME"
            self._record_reject(sym, signal_time, reason)
            return False, reason
        self._roll_day(when)
        rp = float(risk_percent if risk_percent is not None else risk_percent_for(sym))

        # Strategy drawdown halt
        strat_dd = self.strategy_peak_r - self.strategy_equity_r
        if strat_dd + 1e-12 >= STRATEGY_DD_HALT_R:
            reason = REJECT_STRATEGY_DD
            self._record_reject(sym, when.isoformat(), reason, extra={"strategy_dd_r": strat_dd})
            return False, reason

        # Symbol drawdown halt
        seq = float(self.symbol_equity_r.get(sym, 0.0))
        spk = float(self.symbol_peak_r.get(sym, 0.0))
        sym_dd = spk - seq
        if sym_dd + 1e-12 >= SYMBOL_DD_HALT_R:
            reason = REJECT_SYMBOL_DD
            self._record_reject(sym, when.isoformat(), reason, extra={"symbol_dd_r": sym_dd})
            return False, reason

        # Symbol consecutive-loss pause
        pause_until = self.symbol_pause_until.get(sym)
        if pause_until is not None and when < pause_until:
            reason = REJECT_SYMBOL_STREAK
            self._record_reject(
                sym,
                when.isoformat(),
                reason,
                extra={"pause_until": pause_until.isoformat()},
            )
            return False, reason

        # Daily loss halt
        if self.day_pnl_r <= -abs(DAILY_LOSS_HALT_R) + 1e-12:
            reason = REJECT_DAILY_LOSS
            self._record_reject(
                sym, when.isoformat(), reason, extra={"day_pnl_r": self.day_pnl_r}
            )
            return False, reason

        # Already open on symbol
        if sym in self.open:
            reason = "ALREADY_OPEN"
            self._record_reject(sym, when.isoformat(), reason)
            return False, reason

        # Max concurrent across freeze trio
        if len(self.open) >= MAX_CONCURRENT_CLUSTER:
            reason = REJECT_MAX_CONCURRENT
            self._record_reject(
                sym,
                when.isoformat(),
                reason,
                extra={
                    "open_count": len(self.open),
                    "open_symbols": sorted(self.open),
                    "cap_positions": MAX_CONCURRENT_CLUSTER,
                },
            )
            return False, reason

        # Cluster heat (risk % of equity)
        if self.open_risk_pct() + rp > CLUSTER_HEAT_CAP + 1e-12:
            reason = REJECT_CLUSTER_HEAT
            self._record_reject(
                sym,
                when.isoformat(),
                reason,
                extra={
                    "open_risk_pct": self.open_risk_pct(),
                    "requested_risk_pct": rp,
                    "cap": CLUSTER_HEAT_CAP,
                },
            )
            return False, reason

        self.open[sym] = OpenSlot(
            symbol=sym,
            opened_at=when,
            risk_percent=rp,
            entry_key=str(entry_key or ""),
        )
        return True, "OK"

    def on_close(
        self,
        *,
        symbol: str,
        exit_time: datetime | str,
        r_multiple: float,
    ) -> None:
        sym = str(symbol or "").upper()
        when = _parse_ts(exit_time) or datetime.now(timezone.utc)
        self._roll_day(when)
        self.open.pop(sym, None)
        r = float(r_multiple)
        self.day_pnl_r += r
        self.strategy_equity_r += r
        if self.strategy_equity_r > self.strategy_peak_r:
            self.strategy_peak_r = self.strategy_equity_r
        seq = float(self.symbol_equity_r.get(sym, 0.0)) + r
        self.symbol_equity_r[sym] = seq
        spk = float(self.symbol_peak_r.get(sym, 0.0))
        if seq > spk:
            self.symbol_peak_r[sym] = seq
        if r < 0:
            streak = int(self.consecutive_losses.get(sym, 0)) + 1
            self.consecutive_losses[sym] = streak
            if streak >= CONSECUTIVE_LOSS_HALT:
                self.symbol_pause_until[sym] = when + CONSECUTIVE_LOSS_PAUSE
        else:
            self.consecutive_losses[sym] = 0

    def _record_reject(
        self,
        symbol: str,
        signal_time: Any,
        reason: str,
        *,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        self.reject_counts[reason] = int(self.reject_counts.get(reason, 0)) + 1
        row = {
            "symbol": symbol,
            "signal_time": signal_time,
            "reason": reason,
        }
        if extra:
            row.update(dict(extra))
        self.rejects.append(row)


def replay_candidates_with_risk_controls(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Chronologically accept/reject COMBO_02 candidate trades under v1.1 controls.

    Each candidate needs: symbol, signal_time, exit_time, r_multiple (gross or net).
    Optional: risk_percent (defaults to frozen per-symbol profile).
    """
    state = RiskControlledState()
    accepted: list[dict[str, Any]] = []

    def _sort_key(c: Mapping[str, Any]) -> tuple:
        st = _parse_ts(c.get("signal_time")) or datetime.min.replace(tzinfo=timezone.utc)
        return (st, str(c.get("symbol") or ""))

    # Process opens in signal-time order; closes applied when their exit is reached
    # by scanning events.
    events: list[tuple[datetime, str, dict[str, Any]]] = []
    for c in candidates:
        st = _parse_ts(c.get("signal_time"))
        et = _parse_ts(c.get("exit_time"))
        if st is None:
            continue
        events.append((st, "open", dict(c)))
        if et is not None:
            events.append((et, "close", dict(c)))
    events.sort(key=lambda x: (x[0], 0 if x[1] == "close" else 1, str(x[2].get("symbol"))))

    open_keys: set[str] = set()
    for when, kind, c in events:
        sym = str(c.get("symbol") or "").upper()
        key = f"{sym}|{c.get('signal_time')}"
        if kind == "close":
            if key not in open_keys:
                continue
            open_keys.discard(key)
            r = c.get("r_net")
            if r is None:
                r = c.get("r_multiple")
            state.on_close(symbol=sym, exit_time=when, r_multiple=float(r or 0.0))
            continue

        # open
        ok, reason = state.try_open(
            symbol=sym,
            signal_time=when,
            risk_percent=c.get("risk_percent"),
            entry_key=key,
        )
        if not ok:
            continue
        open_keys.add(key)
        row = dict(c)
        row["strategy_id"] = STRATEGY_ID
        row["risk_control_accept_reason"] = reason
        row["risk_percent"] = float(
            c.get("risk_percent") if c.get("risk_percent") is not None else risk_percent_for(sym)
        )
        accepted.append(row)

    return {
        "strategy_id": STRATEGY_ID,
        "variant_version": VARIANT_VERSION,
        "accepted_trades": accepted,
        "rejected": list(state.rejects),
        "reject_counts": dict(state.reject_counts),
        "policy": profile_summary(),
    }


def profile_summary() -> dict[str, Any]:
    return {
        "strategy_id": STRATEGY_ID,
        "parent_strategy_id": PARENT_STRATEGY_ID,
        "parent_combo_version": PARENT_COMBO_VERSION,
        "variant_version": VARIANT_VERSION,
        "combo_id": COMBO_ID,
        "direction": "LONG",
        "symbols": list(SYMBOLS),
        "setup_tf": SETUP_TF,
        "signal_rules": "Reuse COMBO_02 / COMBO_02_V1 exactly (no signal changes)",
        "display": (
            "BTC 1.5% / ETH 0.5% / SOL 0.5% · cluster ≤4% equity · max 2 concurrent"
        ),
        "example_equity_usd": 1000,
        "example_risk_usd": 15,  # BTC 1.5% of $1000
        "risk_percent_by_symbol": dict(RISK_PERCENT_BY_SYMBOL),
        "cluster_heat_cap": CLUSTER_HEAT_CAP,
        "max_concurrent_cluster": MAX_CONCURRENT_CLUSTER,
        "reject_reasons": {
            "max_concurrent": REJECT_MAX_CONCURRENT,
            "cluster_heat": REJECT_CLUSTER_HEAT,
        },
        "daily_loss_halt_r": DAILY_LOSS_HALT_R,
        "consecutive_loss_halt": CONSECUTIVE_LOSS_HALT,
        "consecutive_loss_pause_hours": CONSECUTIVE_LOSS_PAUSE.total_seconds() / 3600.0,
        "strategy_dd_halt_r": STRATEGY_DD_HALT_R,
        "symbol_dd_halt_r": SYMBOL_DD_HALT_R,
        "oos_window": {
            "start": f"{OOS_START}T00:00:00Z",
            "end_inclusive": f"{OOS_END}T23:59:59Z",
            "role": "formal_acceptance",
        },
        "smoke_window": {
            "start": f"{SMOKE_OOS_START}T00:00:00Z",
            "end_inclusive": f"{SMOKE_OOS_END}T23:59:59Z",
            "role": "operational_smoke_only",
            "acceptance": False,
        },
        "sample_rules": {
            "ready_for_paper_min_combined": MIN_SAMPLE_READY_FOR_PAPER,
            "pass_with_review_min_combined": MIN_SAMPLE_PASS_WITH_REVIEW,
            "per_symbol_claim_min": MIN_SAMPLE_PER_SYMBOL_CLAIM,
            "max_drawdown_r": MAX_DD_R,
            "max_losing_streak": MAX_LOSE_STREAK,
            "min_completeness": MIN_COMPLETENESS,
        },
        "diagnostic_failed_v1_oos": {
            "start": DIAGNOSTIC_FAILED_V1_OOS_START,
            "end": DIAGNOSTIC_FAILED_V1_OOS_END,
            "acceptance": False,
        },
        "halts_block_entries_only": True,
    }


def oos_window_start_utc(start_date: str = OOS_START) -> datetime:
    """Inclusive calendar start as 00:00:00 UTC on start_date."""
    return datetime.fromisoformat(f"{start_date}T00:00:00+00:00")


def oos_window_end_utc(end_date: str = OOS_END) -> datetime:
    """Inclusive calendar end as 23:59:59 UTC on end_date."""
    day = datetime.fromisoformat(f"{end_date}T00:00:00+00:00")
    return day.replace(hour=23, minute=59, second=59)


def window_has_started(
    *,
    start_date: str,
    now: datetime | None = None,
) -> bool:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return now_utc >= oos_window_start_utc(start_date)


def window_has_elapsed(
    *,
    end_date: str,
    now: datetime | None = None,
) -> bool:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return now_utc > oos_window_end_utc(end_date)


def is_failed_parent_oos_window(start: str, end: str) -> bool:
    return (
        str(start) == DIAGNOSTIC_FAILED_V1_OOS_START
        and str(end) == DIAGNOSTIC_FAILED_V1_OOS_END
    )


def assert_not_failed_parent_for_acceptance(start: str, end: str) -> None:
    if is_failed_parent_oos_window(start, end):
        raise ValueError(
            "Failed parent COMBO_02_V1 OOS window "
            f"{DIAGNOSTIC_FAILED_V1_OOS_START}→{DIAGNOSTIC_FAILED_V1_OOS_END} "
            "must not be used for v1.1 acceptance."
        )


def classify_v1_1_oos(
    *,
    combined_trades: int,
    average_net_r: float | None,
    max_drawdown_r: float,
    max_losing_streak: int,
    data_completeness_ok: bool,
    lookahead_ok: bool,
    gate_integrity_ok: bool = True,
    window_end_date: str = OOS_END,
    window_start_date: str = OOS_START,
    now: datetime | None = None,
    per_symbol_trades: Mapping[str, int] | None = None,
    acceptance_run: bool = True,
) -> dict[str, Any]:
    """Classify a v1.1 OOS result under sample + validation-state guards.

    Returns status in:
      READY_FOR_PAPER | OOS_PASS_WITH_REVIEW | BLOCKED | INSUFFICIENT_SAMPLE
    """
    hard: list[str] = []
    reviews: list[str] = []
    guards: list[str] = []

    if acceptance_run:
        try:
            assert_not_failed_parent_for_acceptance(window_start_date, window_end_date)
        except ValueError as exc:
            return {
                "status": "INSUFFICIENT_SAMPLE",
                "hard_fails": [str(exc)],
                "reviews": [],
                "guards": ["FAILED_PARENT_WINDOW_FORBIDDEN"],
                "acceptance": False,
            }

    if not window_has_started(start_date=window_start_date, now=now):
        guards.append("WINDOW_NOT_STARTED")
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "hard_fails": hard,
            "reviews": reviews,
            "guards": guards,
            "acceptance": False,
            "reason": "Window start has not been reached; run disabled.",
        }

    elapsed = window_has_elapsed(end_date=window_end_date, now=now)
    if not elapsed:
        guards.append("WINDOW_NOT_ELAPSED")
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "hard_fails": hard,
            "reviews": reviews,
            "guards": guards,
            "acceptance": False,
            "reason": "Window end has not passed; do not classify pass/fail.",
        }

    if not data_completeness_ok:
        guards.append("DATA_COMPLETENESS_BELOW_MIN")
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "hard_fails": hard + ["DATA_HEALTH: completeness < 99%"],
            "reviews": reviews,
            "guards": guards,
            "acceptance": False,
            "reason": "Completeness < 99%; do not classify pass/fail.",
        }

    if not lookahead_ok:
        guards.append("LOOKAHEAD_NOT_PASS")
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "hard_fails": hard + ["LOOKAHEAD_FAIL"],
            "reviews": reviews,
            "guards": guards,
            "acceptance": False,
            "reason": "Lookahead validation failed; do not classify pass/fail.",
        }

    n = int(combined_trades)
    if n < MIN_SAMPLE_PASS_WITH_REVIEW:
        guards.append("COMBINED_TRADES_BELOW_15")
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "hard_fails": hard,
            "reviews": reviews,
            "guards": guards,
            "acceptance": False,
            "reason": f"Combined closed trades {n} < {MIN_SAMPLE_PASS_WITH_REVIEW}",
        }

    if not gate_integrity_ok:
        hard.append("GATE_INTEGRITY_FAIL")
    if average_net_r is None or average_net_r <= 0:
        hard.append(f"NET_EXPECTANCY: average_net_R={average_net_r}")
    if float(max_drawdown_r) > MAX_DD_R + 1e-9:
        hard.append(f"MAX_DD={max_drawdown_r} > {MAX_DD_R}")
    if int(max_losing_streak) > MAX_LOSE_STREAK:
        hard.append(f"LOSE_STREAK={max_losing_streak} > {MAX_LOSE_STREAK}")

    for sym, tn in (per_symbol_trades or {}).items():
        if int(tn) < MIN_SAMPLE_PER_SYMBOL_CLAIM:
            reviews.append(
                f"{sym}:n={tn}<{MIN_SAMPLE_PER_SYMBOL_CLAIM} (no per-symbol claim)"
            )

    if hard:
        return {
            "status": "BLOCKED",
            "hard_fails": hard,
            "reviews": reviews,
            "guards": guards,
            "acceptance": True,
        }

    if n < MIN_SAMPLE_READY_FOR_PAPER:
        return {
            "status": "OOS_PASS_WITH_REVIEW",
            "hard_fails": hard,
            "reviews": reviews
            + [f"combined_n={n}<{MIN_SAMPLE_READY_FOR_PAPER}"],
            "guards": guards,
            "acceptance": True,
        }

    return {
        "status": "READY_FOR_PAPER",
        "hard_fails": hard,
        "reviews": reviews,
        "guards": guards,
        "acceptance": True,
    }
