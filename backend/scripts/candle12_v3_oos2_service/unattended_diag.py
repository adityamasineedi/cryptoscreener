"""Diagnostic for unattended (S4U / no-logon) execution of the CANDLE12_V3 OOS-2 tasks.

It is read-only apart from one file in the ignored log directory. It never starts a
runner, never opens the OOS store, never installs anything, and never prints a
secret. It checks:
- the interpreter: the venv launcher, the resolved Microsoft Store base interpreter,
  and the version;
- the required imports;
- the configuration: DATABASE_URL is readable, and only host, port and database name
  are reported;
- DNS and HTTPS to the Binance endpoints the runner and backfill use;
- a localhost PostgreSQL connection over the read-only research connection;
- that the log directory is writable.

The result goes to service_logs/unattended_diag_<UTC>.json, and the process exits 0
only when every check passes.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
LOGS = BACKEND / "data" / "candle12_v3_paper" / "service_logs"


def main() -> int:
    started = datetime.now(timezone.utc)
    checks: dict[str, dict] = {}

    def check(name: str, fn) -> None:
        t = time.time()
        try:
            checks[name] = {"ok": True, **(fn() or {})}
        except Exception as exc:  # noqa: BLE001 - every failure is a reported result
            checks[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300],
                            "trace": traceback.format_exc()[-600:]}
        checks[name]["seconds"] = round(time.time() - t, 2)

    def interpreter():
        return {"executable": sys.executable, "base_executable": getattr(sys, "_base_executable", None),
                "prefix": sys.prefix, "base_prefix": sys.base_prefix, "version": sys.version.split()[0],
                "user": os.environ.get("USERNAME"), "userprofile": os.environ.get("USERPROFILE"),
                "session_name": os.environ.get("SESSIONNAME"), "cwd": os.getcwd()}

    def imports():
        sys.path.insert(0, str(BACKEND))
        import asyncpg  # noqa: F401
        import httpx  # noqa: F401
        import psutil  # noqa: F401

        from app.research.candle12_v3_paper import backfill, cli, contract  # noqa: F401
        return {"modules": ["asyncpg", "httpx", "psutil", "candle12_v3_paper.cli/backfill/contract"]}

    def config():
        sys.path.insert(0, str(BACKEND / "scripts" / "strategy_rank" / "multicap"))
        from urllib.parse import urlparse

        import _db

        u = urlparse(_db._dsn())
        env_file = BACKEND.parent / ".env"
        return {"database_host": u.hostname, "database_port": u.port, "database_name": u.path.lstrip("/"),
                "env_file_readable": env_file.exists() and os.access(env_file, os.R_OK),
                "credentials": "present (not printed)" if u.password else "absent"}

    def network():
        import httpx

        ip = socket.getaddrinfo("fapi.binance.com", 443, proto=socket.IPPROTO_TCP)[0][4][0]
        out = {"dns_fapi.binance.com": ip}
        with httpx.Client(timeout=15) as c:
            for path, params in (("/fapi/v1/time", {}), ("/fapi/v1/klines", {"symbol": "BTCUSDT", "interval": "1h", "limit": 1}),
                                 ("/fapi/v1/fundingRate", {"symbol": "BTCUSDT", "limit": 1})):
                r = c.get(f"https://fapi.binance.com{path}", params=params)
                out[path] = r.status_code
                r.raise_for_status()
        return out

    def postgres():
        import asyncio

        sys.path.insert(0, str(BACKEND / "scripts" / "strategy_rank" / "multicap"))
        import _db

        async def go():
            conn = await _db._connect()  # read-only session
            try:
                return {"select_1": await conn.fetchval("SELECT 1"),
                        "read_only": await conn.fetchval("SHOW default_transaction_read_only"),
                        "ohlcv_latest_btc_1h": str(await conn.fetchval(
                            "SELECT max(time) FROM ohlcv WHERE symbol='BTCUSDT' AND timeframe='1h'"))}
            finally:
                await conn.close()

        return asyncio.run(go())

    def log_dir():
        LOGS.mkdir(parents=True, exist_ok=True)
        probe = LOGS / f".diag_probe_{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        ok = probe.read_text(encoding="utf-8") == "ok"
        probe.unlink()
        return {"dir": str(LOGS), "write_read_delete": ok}

    for name, fn in (("interpreter", interpreter), ("imports", imports), ("config", config), ("network", network),
                     ("postgres", postgres), ("log_dir", log_dir)):
        check(name, fn)
    result = {"started": started.isoformat(), "finished": datetime.now(timezone.utc).isoformat(),
              "all_ok": all(c["ok"] for c in checks.values()), "checks": checks}
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        (LOGS / f"unattended_diag_{started.strftime('%Y%m%dT%H%M%SZ')}.json").write_text(
            json.dumps(result, indent=1, default=str), encoding="utf-8")
    except OSError:
        pass
    print(json.dumps(result, default=str))
    return 0 if result["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
