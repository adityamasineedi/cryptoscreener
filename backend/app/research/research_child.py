"""Run one research function in a child interpreter, off the API process.

Pure-Python research work (analytics attach, VectorBT replay, loss what-ifs)
holds the GIL. A worker thread in the API process therefore starves the event
loop: health checks time out and the paper watcher misses its bar. This module
runs ``module:function(payload)`` in its own ``python -m`` child instead.

IPC is the same tempfile-pickle pattern the walk uses (no pipes, no
ProcessPoolExecutor, which fails with handle errors on Windows): the payload
goes in as a pickle file, the result comes back as a pickle file, and every
temp file is deleted. The function and its payload are unchanged, so the
result is the object the in-process call would have returned.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import pickle
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable

_BACKEND = Path(__file__).resolve().parents[2]


class ResearchChildCancelled(Exception):
    """The caller cancelled; the child process was killed."""


class ResearchChildError(RuntimeError):
    """The child failed and its exception could not be passed back as-is."""


class _ChildDidNotStart(ResearchChildError):
    """Windows failed the child's process start; the target never ran."""


# STATUS_DLL_INIT_FAILED (0xC0000142): seen when the machine is short of
# desktop heap / process slots. The interpreter never reached the target.
_NOT_STARTED_CODES = frozenset({0xC0000142})
_START_ATTEMPTS = 3


def _write_pickle(obj: Any, prefix: str) -> str:
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=".pkl")
    with os.fdopen(fd, "wb") as handle:
        pickle.dump(obj, handle, protocol=pickle.HIGHEST_PROTOCOL)
    return name


def _unlink(path: str | None, *, retry_seconds: float = 0.0) -> None:
    """Delete ``path``; retry while a just-killed child still holds it open (Windows)."""
    if not path:
        return
    deadline = time.perf_counter() + retry_seconds
    while True:
        try:
            Path(path).unlink()
            return
        except FileNotFoundError:
            return
        except OSError:
            if time.perf_counter() >= deadline:
                return
            time.sleep(0.05)


def _resolve(target: str) -> Callable[[Any], Any]:
    module_name, _, func_name = target.partition(":")
    if not module_name or not func_name:
        raise ValueError(f"target must be 'module:function', got {target!r}")
    return getattr(importlib.import_module(module_name), func_name)


def _raise_child_failure(result: dict[str, Any]) -> None:
    exc = result.get("exception")
    tb = str(result.get("traceback") or "")
    if isinstance(exc, BaseException):
        raise exc from ResearchChildError(f"child traceback:\n{tb}")
    raise ResearchChildError(f"{result.get('error') or 'child failed'}\n{tb[-2000:]}")


def _run_child_once(
    target: str,
    payload_path: str,
    *,
    should_cancel: Callable[[], bool] | None = None,
    heartbeat: Callable[[], None] | None = None,
    heartbeat_every: float = 2.0,
    poll_seconds: float = 0.05,
) -> Any:
    """One child start; see ``run_child_sync``."""
    from app.research.backtest_cpu_pool import _spawn_python_executable

    exe = _spawn_python_executable() or sys.executable
    fd, result_path = tempfile.mkstemp(prefix="research_child_", suffix=".pkl")
    os.close(fd)
    # stderr goes to a file, not a pipe: a chatty child cannot fill a pipe and block.
    err_fd, stderr_path = tempfile.mkstemp(prefix="research_child_", suffix=".err")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_BACKEND) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.Popen(
            [exe, "-m", "app.research.research_child", target, payload_path, result_path],
            cwd=str(_BACKEND),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=err_fd,
        )
    except BaseException:
        os.close(err_fd)
        _unlink(stderr_path)
        _unlink(result_path)
        raise
    os.close(err_fd)
    last_beat = time.perf_counter()
    killed = False
    try:
        while proc.poll() is None:
            if should_cancel is not None and should_cancel():
                proc.kill()
                killed = True
                raise ResearchChildCancelled(target)
            if heartbeat is not None and time.perf_counter() - last_beat >= heartbeat_every:
                try:
                    heartbeat()
                except Exception:  # noqa: BLE001
                    pass
                last_beat = time.perf_counter()
            time.sleep(poll_seconds)
        result_file = Path(result_path)
        if not (result_file.exists() and result_file.stat().st_size > 0):
            try:
                stderr = Path(stderr_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                stderr = ""
            error = _ChildDidNotStart if proc.returncode in _NOT_STARTED_CODES else ResearchChildError
            raise error(
                f"research child {target} exited {proc.returncode} via {exe} without a result. "
                f"{stderr[-800:]}"
            )
        with result_file.open("rb") as handle:
            result = pickle.load(handle)
        if not result.get("ok"):
            _raise_child_failure(result)
        return result["value"]
    finally:
        if proc.poll() is None:
            proc.kill()
            killed = True
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        # The venv launcher's interpreter dies with it but may hold the files briefly.
        grace = 3.0 if killed else 0.0
        _unlink(result_path, retry_seconds=grace)
        _unlink(stderr_path, retry_seconds=grace)


def run_child_sync(
    target: str,
    payload_path: str,
    *,
    should_cancel: Callable[[], bool] | None = None,
    heartbeat: Callable[[], None] | None = None,
    heartbeat_every: float = 2.0,
    poll_seconds: float = 0.05,
) -> Any:
    """Run ``target`` on the pickled payload in a child; block until it ends.

    Blocking but GIL-free: the calling thread only polls the child. A cancel
    kills the child and raises ``ResearchChildCancelled``. A child that Windows
    could not start (the target never ran) is started again, up to
    ``_START_ATTEMPTS`` times.
    """
    for attempt in range(1, _START_ATTEMPTS + 1):
        try:
            return _run_child_once(
                target,
                payload_path,
                should_cancel=should_cancel,
                heartbeat=heartbeat,
                heartbeat_every=heartbeat_every,
                poll_seconds=poll_seconds,
            )
        except _ChildDidNotStart:
            if attempt == _START_ATTEMPTS:
                raise
            if heartbeat is not None:
                try:
                    heartbeat()
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(1.0 * attempt)
    raise AssertionError("unreachable")


async def run_in_child(
    target: str,
    payload: Any,
    *,
    should_cancel: Callable[[], bool] | None = None,
    heartbeat: Callable[[], None] | None = None,
    heartbeat_every: float = 2.0,
) -> Any:
    """Async wrapper: payload to a temp pickle, child run, temp files removed."""
    payload_path = await asyncio.to_thread(_write_pickle, payload, "research_child_in_")
    try:
        return await asyncio.to_thread(
            run_child_sync,
            target,
            payload_path,
            should_cancel=should_cancel,
            heartbeat=heartbeat,
            heartbeat_every=heartbeat_every,
        )
    finally:
        _unlink(payload_path, retry_seconds=3.0)


def _cli(argv: list[str]) -> int:
    """Child entry: ``target payload.pkl result.pkl``."""
    if len(argv) != 4:
        return 2
    target, payload_path, result_path = argv[1], argv[2], argv[3]
    os.environ["RESEARCH_CPU_WORKER"] = "1"
    try:
        with Path(payload_path).open("rb") as handle:
            payload = pickle.load(handle)
        result: dict[str, Any] = {"ok": True, "value": _resolve(target)(payload)}
    except BaseException as exc:  # noqa: BLE001
        result = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-4000:],
            "exception": exc,
        }
    try:
        blob = pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL)
        if not result.get("ok"):
            pickle.loads(blob)  # some exception types pickle but cannot be rebuilt
    except Exception:  # noqa: BLE001
        # An unpicklable exception (or value) still reports why the child failed.
        result.pop("exception", None)
        if result.get("ok"):
            result = {"ok": False, "error": "result is not picklable", "traceback": traceback.format_exc()}
        blob = pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL)
    Path(result_path).write_bytes(blob)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
