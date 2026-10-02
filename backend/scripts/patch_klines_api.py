from __future__ import annotations

import re
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "app" / "ingestion" / "binance_rest.py"
text = p.read_text(encoding="utf-8")
text = re.sub(r"\n{3,}", "\n\n", text)

new = '''async def futures_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[Any]:
        params: dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        return await self._request(
            "GET",
            self.settings.binance_futures_rest,
            "/fapi/v1/klines",
            params=params,
            weight=5 if limit < 100 else 10,
        )'''

pat = re.compile(
    r"async def futures_klines\(\s*self, symbol: str, interval: str, limit: int = 500\s*\) -> list\[Any\]:\s*"
    r"return await self\._request\(\s*\"GET\",\s*self\.settings\.binance_futures_rest,\s*\"/fapi/v1/klines\",\s*"
    r"params=\{\"symbol\": symbol, \"interval\": interval, \"limit\": limit\},\s*"
    r"weight=5 if limit < 100 else 10,\s*\)",
    re.M,
)
if not pat.search(text):
    raise SystemExit("futures_klines pattern not found")
p.write_text(pat.sub(new, text, count=1), encoding="utf-8")
print("ok")
