"""Bounded process-pool isolation for research backtest CPU work.

Single-flight: max_workers=1. Filesystem cancel/progress IPC (no Manager).
Payload blob is pickle so candle timestamps/types are preserved exactly.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import multiprocessing as mp
import multiprocessing.spawn as mp_spawn
import os
import pickle
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.research.backtest_cpu_worker import run_combination_backtest_worker

_MAX_WORKERS = 1

_lock = threading.Lock()
_pool: ProcessPoolExecutor | None = None
_ctx: mp.context.BaseContext | None = None


def _python_file_ok(path: str) -> bool:
    try:
        return bool(path) and os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _spawn_python_executable() -> str | None:
    """A real python.exe. The Windows Store alias is a 0-byte reparse point.

    Uvicorn's reloader starts the app with that alias. Multiprocessing then
    calls CreateProcess on it and Windows returns
    ``OSError: [Errno 22] Invalid argument``. Patching ``_base_executable``
    is not enough: when the process itself is the alias, spawn uses
    ``sys.executable`` and ignores the base path.
    """
    if os.name != "nt":
        return None
    venv = os.environ.get("VIRTUAL_ENV") or ""
    prefix = getattr(sys, "prefix", "") or ""
    script_dir = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else ""
    from_path = ""
    for entry in sys.path:
        norm = str(entry).replace("/", "\\")
        lower = norm.lower()
        if lower.endswith("\\lib\\site-packages"):
            from_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(norm))), "Scripts", "python.exe")
            break
    candidates = [
        from_path,
        os.path.join(prefix, "Scripts", "python.exe") if prefix else "",
        os.environ.get("__PYVENV_LAUNCHER__") or "",
        os.path.join(script_dir, "python.exe") if script_dir else "",
        os.path.join(venv, "Scripts", "python.exe") if venv else "",
        sys.executable,
        os.path.join(getattr(sys, "base_prefix", "") or "", "python.exe"),
    ]
    for path in candidates:
        if _python_file_ok(path):
            return path
    return None


def _prefer_real_windows_python() -> None:
    chosen = _spawn_python_executable()
    if not chosen:
        return
    if not _python_file_ok(getattr(sys, "_base_executable", "") or ""):
        sys._base_executable = chosen
    try:
        current_spawn = mp_spawn.get_executable()
    except Exception:  # noqa: BLE001
        current_spawn = ""
    if not _python_file_ok(current_spawn):
        mp_spawn.set_executable(chosen)


def _mp_context() -> mp.context.BaseContext:
    global _ctx
    if _ctx is None:
        _prefer_real_windows_python()
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
    global _pool, _ctx
    with _lock:
        if _pool is not None:
            try:
                _pool.shutdown(wait=wait, cancel_futures=True)
            except TypeError:
                _pool.shutdown(wait=wait)
            _pool = None
        # The spawn context captured the interpreter. A retry must build a
        # new one, or the next pool hits the same CreateProcess failure.
        _ctx = None


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
    candles_5m: Sequence[Mapping[str, Any]] | None = None,
    candles_1d: Sequence[Mapping[str, Any]] | None = None,
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
        "candles_5m": _candles_plain(candles_5m),
        "candles_1d": _candles_plain(candles_1d),
        "job_id": job_id,
        "cancel_path": cancel_path,
        "progress_path": progress_path,
    }

    blob_path = await asyncio.to_thread(_write_payload_blob, payload)

    def _run_windows_subprocess() -> dict[str, Any]:
        """Run the worker without a process-pool semaphore.

        The API process is the Windows Store alias. A pool child of that
        process dies with WinError 6 (invalid handle) or Errno 22.
        """
        exe = _spawn_python_executable() or sys.executable
        fd, result_path = tempfile.mkstemp(prefix="bt_result_", suffix=".pkl")
        os.close(fd)
        backend = str(Path(__file__).resolve().parents[2])
        env = os.environ.copy()
        env["PYTHONPATH"] = backend + os.pathsep + env.get("PYTHONPATH", "")
        if progress_callback is not None:
            try:
                progress_callback({"phase": "CONTEXT_BUILD"})
            except Exception:  # noqa: BLE001
                pass
        proc = subprocess.Popen(
            [exe, "-m", "app.research.backtest_cpu_worker", blob_path, result_path],
            cwd=backend,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        last_keep_alive = time.perf_counter()
        try:
            while proc.poll() is None:
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
                    progress = Path(progress_path)
                    if progress.exists():
                        try:
                            progress_callback(json.loads(progress.read_text(encoding="utf-8")))
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
                time.sleep(0.05)
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            result_file = Path(result_path)
            if result_file.exists() and result_file.stat().st_size > 0:
                with result_file.open("rb") as handle:
                    return pickle.load(handle)
            raise OSError(
                0,
                f"Backtest worker exited {proc.returncode} via {exe}. {stderr[-500:]}",
            )
        finally:
            if proc.poll() is None:
                proc.kill()
            try:
                Path(result_path).unlink()
            except OSError:
                pass

    if os.name == "nt":
        try:
            return await asyncio.to_thread(_run_windows_subprocess)
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

    def _submit_result() -> dict[str, Any]:
        # The pool must already exist. Creating it on this worker thread makes
        # Windows return Errno 22 / WinError 6.
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

    # Create the pool on the event-loop thread. A pool born inside
    # asyncio.to_thread cannot spawn on Windows.
    get_research_cpu_pool()
    try:
        try:
            return await asyncio.to_thread(_submit_result)
        except (OSError, BrokenProcessPool):
            shutdown_research_cpu_pool(wait=False)
            get_research_cpu_pool()
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
