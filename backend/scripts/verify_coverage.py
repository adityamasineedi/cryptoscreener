from __future__ import annotations

import json
import urllib.request


def get(path: str):
    with urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=60) as r:
        return json.loads(r.read().decode())


def main() -> None:
    for path in [
        "/api/system/stats",
        "/api/data/coverage",
        "/api/data/ohlcv-coverage",
        "/api/health/providers",
        "/api/system/performance",
    ]:
        data = get(path)
        print("====", path)
        print(json.dumps(data, indent=2)[:2200])
        print()


if __name__ == "__main__":
    main()
