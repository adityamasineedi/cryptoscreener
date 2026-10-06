"""Bounded process-pool isolation for research backtest CPU work.

Single-flight: max_workers=1. Filesystem cancel/progress IPC (no Manager).
Payload blob is pickle so candle timestamps/types are preserved exactly.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import multiprocessing as mp
import os
import pickle
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.research.backtest_cpu_worker import run_combination_backtest_worker

_MAX_WORKERS = 1

_lock = threading.Lock()
_pool: ProcessPoolExecutor | None = None
_ctx: mp.context.BaseContext | None = None


def _mp_context() -> mp.context.BaseContext:
    global _ctx
    if _ctx is None:
        _ctx = mp.get_context("spawn")
    return _ctx


def get_research_cpu_pool() -> ProcessPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(
                max_workers=_MAX_WORKERS,
                mp_context=_mp_context(),
            )
        return _pool


def shutdown_research_cpu_pool(*, wait: bool = False) -> None:
    global _pool
    with _lock:
        if _pool is not None:
            try:
                _pool.shutdown(wait=wait, cancel_futures=True)
            except TypeError:
                _pool.shutdown(wait=wait)
            _pool = None


atexit.register(lambda: shutdown_research_cpu_pool(wait=False))


class FileCancelEvent:
    """Cooperative cancel flag shared via filesystem path."""

    __slots__ = ("path",)

    def __init__(self, path: str | None = None) -> None:
        if path is None:
            fd, name = tempfile.mkstemp(prefix="bt_cancel_", suffix=".flag")
            os.close(fd)
            try:
                os.unlink(name)
            except OSError:
                pass
            self.path = name
        else:
            self.path = path

    def set(self) -> None:
        Path(self.path).write_text("1", encoding="utf-8")

    def is_set(self) -> bool:
        return Path(self.path).exists()

    def clear(self) -> None:
        p = Path(self.path)
        if p.exists():
            p.unlink()


def new_cancel_event() -> FileCancelEvent:
    return FileCancelEvent()


def pool_stats() -> dict[str, Any]:
    with _lock:
        alive = _pool is not None
    return {
        "isolation": "process_pool",
        "max_workers": _MAX_WORKERS,
        "pool_started": alive,
        "research_only": True,
        "ipc": "tempfile_pickle_payload",
        "note": "CPU-bound run_combination_backtest runs off the API event loop",
    }


def _candles_plain(
    rows: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    if rows is None:
        return None
    return [dict(r) for r in rows]


def _write_payload_blob(payload: dict[str, Any]) -> str:
    fd, name = tempfile.mkstemp(prefix="bt_payload_", suffix=".pkl")
    os.close(fd)
    path = Path(name)
    with path.open("wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    return str(path)


async def run_combination_backtest_isolated(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    combination_id: str,
    signal_config: Any = None,
    research_config: Any = None,
    market_cap: float | None = None,
    direction_filter: str | None = None,
    index_start: int | None = None,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
    candles_15m: Sequence[Mapping[str, Any]] | None = None,
    job_id: str | None = None,
    cancel_event: Any | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    event = cancel_event if cancel_event is not None else FileCancelEvent()
    cancel_path = event.path if isinstance(event, FileCancelEvent) else str(event)

    progress_path = None
    if progress_callback is not None:
        fd, progress_path = tempfile.mkstemp(prefix="bt_prog_", suffix=".json")
        os.close(fd)
        try:
            os.unlink(progress_path)
        except OSError:
            pass

    payload: dict[str, Any] = {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": _candles_plain(candles) or [],
        "combination_id": combination_id,
        "signal_config": signal_config,
        "research_config": research_config,
        "market_cap": market_cap,
        "direction_filter": direction_filter,
        "index_start": index_start,
        "candles_1h": _candles_plain(candles_1h),
        "candles_4h": _candles_plain(candles_4h),
        "candles_15m": _candles_plain(candles_15m),
        "job_id": job_id,
        "cancel_path": cancel_path,
        "progress_path": progress_path,
    }

    blob_path = await asyncio.to_thread(_write_payload_blob, payload)

    def _submit_result() -> dict[str, Any]:
        pool = get_research_cpu_pool()
        # Keep job watchdog alive during long pre-walk work (e.g. COMBO_02_V2
        # 1h feature-table build can exceed the default 120s heartbeat window
        # before the worker writes its first progress file).
        if progress_callback is not None:
            try:
                progress_callback({"phase": "CONTEXT_BUILD"})
            except Exception:  # noqa: BLE001
                pass
        fut = pool.submit(
            run_combination_backtest_worker,
            {"payload_path": blob_path},
        )
        last_keep_alive = time.perf_counter()
        while True:
            if should_cancel is not None and should_cancel():
                try:
                    if isinstance(event, FileCancelEvent):
                        event.set()
                    else:
                        Path(cancel_path).write_text("1", encoding="utf-8")
                except Exception:  # noqa: BLE001
                    pass
            saw_worker_progress = False
            if progress_callback is not None and progress_path:
                p = Path(progress_path)
                if p.exists():
                    try:
                        progress_callback(json.loads(p.read_text(encoding="utf-8")))
                        saw_worker_progress = True
                        last_keep_alive = time.perf_counter()
                    except Exception:  # noqa: BLE001
                        pass
            if (
                progress_callback is not None
                and not saw_worker_progress
                and (time.perf_counter() - last_keep_alive) >= 15.0
            ):
                try:
                    progress_callback({"phase": "CONTEXT_BUILD"})
                except Exception:  # noqa: BLE001
                    pass
                last_keep_alive = time.perf_counter()
            if fut.done():
                break
            time.sleep(0.05)
        return fut.result()

    try:
        return await asyncio.to_thread(_submit_result)
    finally:
        for p in (blob_path, progress_path):
            if not p:
                continue
            try:
                pp = Path(p)
                if pp.exists():
                    pp.unlink()
            except Exception:  # noqa: BLE001
                pass


async def run_matrix_postprocess_isolated(
    payload: dict[str, Any],
    *,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run fee enrich + analytics attach off the event loop.

    Uses a worker thread (not a second process pickle of multi-10k candle
    arrays) while emitting heartbeats so the job watchdog does not stall.
    Strategy trade results are already finalized before this runs.
    """
    from app.research.backtest_cpu_worker import build_matrix_row_postprocess

    loop = asyncio.get_running_loop()
    done = loop.create_future()

    def _run() -> None:
        try:
            result = build_matrix_row_postprocess(payload)
            loop.call_soon_threadsafe(done.set_result, result)
        except Exception as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(done.set_exception, exc)

    threading.Thread(target=_run, name="matrix_postprocess", daemon=True).start()
    while not done.done():
        if progress_callback is not None:
            try:
                # Phase-only heartbeat: never zero bars/trades — that made the
                # UI look stuck at 0% for the entire analytics attach window.
                progress_callback({"phase": "ANALYTICS_ATTACH"})
            except Exception:  # noqa: BLE001
                pass
        try:
            return await asyncio.wait_for(asyncio.shield(done), timeout=2.0)
        except asyncio.TimeoutError:
            continue
    return done.result()
