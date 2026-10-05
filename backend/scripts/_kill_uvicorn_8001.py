#!/usr/bin/env python3
from __future__ import annotations

import time
import urllib.request

import psutil


def main() -> None:
    print("uvicorn procs:")
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cl = " ".join(p.info.get("cmdline") or [])
        except Exception:
            continue
        if "uvicorn" in cl:
            print(p.pid, cl[:220])
            try:
                for c in p.children(recursive=True):
                    c.kill()
                p.kill()
                print("killed", p.pid)
            except Exception as exc:
                print("kill_fail", p.pid, exc)
    time.sleep(1)
    for port in (8001, 8000):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
            print(port, "up")
        except Exception as exc:
            print(port, "down", type(exc).__name__)


if __name__ == "__main__":
    main()
