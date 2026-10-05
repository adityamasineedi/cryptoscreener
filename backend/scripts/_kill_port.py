#!/usr/bin/env python3
"""Restart helper: kill listeners on a port (Windows)."""

from __future__ import annotations

import sys

import psutil


def main() -> None:
    port = int(sys.argv[1] if len(sys.argv) > 1 else 8001)
    killed = []
    for c in psutil.net_connections(kind="inet"):
        if c.laddr and c.laddr.port == port and c.pid:
            try:
                p = psutil.Process(c.pid)
                for ch in p.children(recursive=True):
                    ch.kill()
                    killed.append(ch.pid)
                p.kill()
                killed.append(c.pid)
            except Exception as exc:  # noqa: BLE001
                print("fail", c.pid, exc)
    # Also match uvicorn cmdline
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cl = " ".join(p.info.get("cmdline") or [])
        except Exception:
            continue
        if "uvicorn" in cl and str(port) in cl:
            try:
                p.kill()
                killed.append(p.pid)
            except Exception:
                pass
    print({"port": port, "killed": killed})


if __name__ == "__main__":
    main()
