"""Research-only Trade Plan / entry-timing forensics.

Does NOT modify live signals, Trade Plan, thresholds, SL/TP, or execution.
Read-only reconstruction from paper/backtest trades + PostgreSQL OHLCV.
"""

from app.research.trade_plan_forensics.service import (
    run_trade_plan_forensics,
    get_trade_forensic_detail,
)

__all__ = ["run_trade_plan_forensics", "get_trade_forensic_detail"]
