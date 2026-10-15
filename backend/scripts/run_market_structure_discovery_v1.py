"""Run bounded market-structure discovery from existing PostgreSQL candles.

Research only. This script reads persisted 1h OHLCV and does not download,
backfill, notify, or grant paper/live execution rights.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.research.config import ResearchConfig  # noqa: E402
from app.research.market_structure_discovery_v1.config import (  # noqa: E402
    MARKETS,
    MAX_CANDLES_PER_MARKET,
    DiscoveryConfig,
)
from app.research.market_structure_discovery_v1.embargo import (  # noqa: E402
    apply_embargo,
)
from app.research.market_structure_discovery_v1.patterns import (  # noqa: E402
    discover_patterns,
)
from app.research.market_structure_discovery_v1.runner import (  # noqa: E402
    run_discovery,
)
from app.research.postgres_ohlcv import load_ohlcv_series  # noqa: E402
from app.research.repository import save_research_run  # noqa: E402
from app.services.database import db_manager  # noqa: E402


TIMEFRAME = "1h"
COMBINATION_IDS = ("COMBO_01", "COMBO_03", "COMBO_03_TRANSITION")
MIN_PATTERN_BARS = 12

Candle = Mapping[str, Any]
CandleLoader = Callable[[str, str], Awaitable[list[dict[str, Any]]]]
DiscoveryRunner = Callable[..., Awaitable[dict[str, Any]]]
PatternDiscoverer = Callable[..., dict[str, Any]]
EmbargoApplier = Callable[..., dict[str, Any]]
RunSaver = Callable[[dict[str, Any]], Awaitable[str]]


def _walk_forward_bars(max_candles: int) -> tuple[int, int]:
    """Choose a small fixed-window count while remaining within config bounds."""
    if max_candles < MIN_PATTERN_BARS:
        raise RuntimeError(
            f"Existing PostgreSQL 1h history is too short: {max_candles} bars; "
            f"at least {MIN_PATTERN_BARS} are required"
        )
    train_bars = max(8, max_candles // 2)
    test_bars = max(2, max_candles // 12)
    return train_bars, test_bars


def _pattern_train_end(candles: Sequence[Candle]) -> int:
    # At least three complete rows remain after five-bar feature warmup.
    return min(len(candles) - 1, max(8, int(len(candles) * 0.70)))


def _safety_stamp(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **dict(payload),
        "operator_approved": False,
        "production_approved": False,
        "paper_eligible": False,
        "live_eligible": False,
        "telegram_eligible": False,
    }


async def run_existing_postgres_discovery(
    *,
    candle_loader: CandleLoader = load_ohlcv_series,
    discovery_runner: DiscoveryRunner = run_discovery,
    pattern_discoverer: PatternDiscoverer = discover_patterns,
    embargo_applier: EmbargoApplier = apply_embargo,
    run_saver: RunSaver = save_research_run,
) -> dict[str, Any]:
    """Load the fixed universe, run discovery, and attach pattern summaries."""
    candles_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for symbol in MARKETS:
        try:
            candles = await candle_loader(symbol, TIMEFRAME)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Existing PostgreSQL 1h candle read path failed for {symbol}: {exc}"
            ) from exc
        if not candles:
            raise RuntimeError(
                f"Existing PostgreSQL 1h candle read path has no rows for {symbol}"
            )
        if len(candles) > MAX_CANDLES_PER_MARKET:
            raise RuntimeError(
                f"Existing PostgreSQL history for {symbol} has {len(candles)} bars; "
                f"DiscoveryConfig limit is {MAX_CANDLES_PER_MARKET}"
            )
        candles_by_symbol[symbol] = candles

    train_bars, test_bars = _walk_forward_bars(
        max(len(candles) for candles in candles_by_symbol.values())
    )
    config = DiscoveryConfig(
        combination_ids=COMBINATION_IDS,
        timeframe=TIMEFRAME,
        research_config=ResearchConfig(
            walk_forward_train_bars=train_bars,
            walk_forward_test_bars=test_bars,
        ),
    )
    # Validates the derived window count before any backtest starts.
    config.validated_combination_ids()
    datasets = {
        symbol: {"candles": candles_by_symbol[symbol]}
        for symbol in MARKETS
    }
    # The CLI owns the single persistence step so purge/embargo metadata is
    # attached before the discovery run is saved. The live walk-forward path is
    # deliberately unchanged.
    summary = await discovery_runner(datasets, config=config, persist=False)

    first_test_start = train_bars
    first_test_end = min(
        max(len(candles) for candles in candles_by_symbol.values()),
        first_test_start + test_bars,
    )
    embargo = _safety_stamp(
        embargo_applier(
            train_start=0,
            train_end=train_bars,
            test_start=first_test_start,
            test_end=first_test_end,
            horizon=1,
            embargo_bars=1,
        )
    )

    by_symbol: dict[str, dict[str, Any]] = {}
    for symbol in MARKETS:
        candles = candles_by_symbol[symbol]
        pattern = pattern_discoverer(
            candles,
            train_end=_pattern_train_end(candles),
            n_clusters=3,
            forward_horizon=1,
        )
        by_symbol[symbol] = _safety_stamp(pattern)

    patterns_summary = _safety_stamp(
        {
            "research_only": True,
            "timeframe": TIMEFRAME,
            "symbols": list(MARKETS),
            "by_symbol": by_symbol,
            "disclaimer": (
                "Research-only descriptive clusters. A cluster is not a strategy, "
                "trade recommendation, or execution approval."
            ),
        }
    )
    updated_summary = _safety_stamp(
        {**summary, "embargo": embargo, "patterns": patterns_summary}
    )
    run_id = str(updated_summary.get("run_id") or "")
    if not run_id:
        raise RuntimeError("Discovery did not return a run id")

    # Reuse the canonical research-run persistence format and the same run id.
    persisted_id = await run_saver(updated_summary)
    if persisted_id != run_id:
        raise RuntimeError("Research repository returned an unexpected run id")
    return updated_summary


async def _main() -> dict[str, Any]:
    await db_manager.connect(get_settings(), ensure_schema=False)
    if db_manager.engine is None:
        raise RuntimeError("Existing PostgreSQL candle read path is unavailable")
    try:
        return await run_existing_postgres_discovery()
    finally:
        await db_manager.close()


def main() -> int:
    try:
        summary = asyncio.run(_main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    print(
        json.dumps(
            {
                "run_id": summary.get("run_id"),
                "symbols": summary.get("symbols"),
                "timeframes": summary.get("timeframes"),
                "operator_approved": False,
                "production_approved": False,
                "paper_eligible": False,
                "live_eligible": False,
                "telegram_eligible": False,
                "disclaimer": (
                    "Research output only; no execution or promotion approval."
                ),
            },
            default=str,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
