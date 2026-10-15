"""Operational safety limits for the V3 paper adapter.

These never change a signal, a price or a size. They can only stop the adapter
from opening something. A stopped entry is recorded as SIGNAL_MISSED with an
open exception, because the engine would have taken it. Protective exits are
always placed: a limit never leaves a position without its stop.

- Kill switch: a file. While it exists, no new entry is placed. Exits, cancels
  and syncs continue.
- Max open trades per symbol.
- Max entry order attempts per UTC day.
- Max consecutive broker errors: the adapter halts, raises an alert and stops
  entering until an operator resolves the HALTED exception.
- Heartbeat: every N minutes while a run is active.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Global: one file stops new entries in every V3 paper run on this machine.
DEFAULT_KILL_SWITCH = Path(__file__).resolve().parents[3] / "data" / "candle12_v3_paper" / "KILL"
KILL_SWITCH = "KILL_SWITCH"
MAX_OPEN_TRADES = "MAX_OPEN_TRADES"
MAX_DAILY_ORDER_ATTEMPTS = "MAX_DAILY_ORDER_ATTEMPTS"
MAX_CONSECUTIVE_BROKER_ERRORS = "MAX_CONSECUTIVE_BROKER_ERRORS"
HALTED = "HALTED"


@dataclass
class SafetyLimits:
    kill_switch_path: Path | None = None
    max_open_trades_per_symbol: int = 1
    max_daily_order_attempts: int = 20
    max_consecutive_broker_errors: int = 5
    heartbeat_minutes: float = 5.0

    def kill_switch_engaged(self) -> bool:
        return self.kill_switch_path is not None and Path(self.kill_switch_path).exists()


def engage_kill_switch(path: Path, *, by: str, reason: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"engaged_at={datetime.now(timezone.utc).isoformat()}\nby={by}\nreason={reason}\n",
                    encoding="utf-8")


def release_kill_switch(path: Path) -> bool:
    if path.exists():
        path.unlink()
        return True
    return False
