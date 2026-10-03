"""Free social/sentiment providers: socialtickers + XOOMAR. Never fabricates metrics."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Settings
from app.core.cache import TTLCache
from app.core.logging import get_logger
from app.core.provider_health import provider_health
from app.core.rate_limiter import RateLimiter, rate_limiters
from app.core.request_audit import request_audit
from app.core.retry import RetryPolicy
from app.models.schemas import DataStatus, FreshValue

logger = get_logger("providers.free_social")

PROVIDER_NAME = "free_social"
KNOWN_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USD")

FIELDS = (
    "social_dominance",
    "social_volume",
    "mentions",
    "engagement",
    "sentiment",
    "sentiment_change",
)

# socialtickers leaderboard field mapping
ST_FIELD_MAP = {
    "social_dominance": "share",
    "social_volume": "mentions",
    "mentions": "mentions",
    "engagement": "upvotes",
}


@dataclass
class SymbolMapping:
    binance_symbol: str
    provider_symbol: str | None
    provider_asset_id: int | str | None
    mapping_status: str


@dataclass
class FreeSocialStats:
    requests_total: int = 0
    requests_success: int = 0
    requests_failed: int = 0
    rate_limit_hits: int = 0
    assets_received: int = 0
    assets_mapped: int = 0
    assets_unmapped: int = 0
    assets_requested: int = 0
    socialtickers_received: int = 0
    xoomar_received: int = 0
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_response_at: str | None = None
    last_error: str | None = None
    last_http_status: int | None = None
    connected: bool = False
    plan_status: str | None = None
    provider_generated_at: str | None = None


def candidate_bases(binance_symbol: str) -> list[str]:
    sym = binance_symbol.upper().strip()
    out: list[str] = []
    if not sym:
        return out
    out.append(sym)
    for q in KNOWN_QUOTES:
        if sym.endswith(q) and len(sym) > len(q):
            base = sym[: -len(q)]
            if base and base not in out:
                out.append(base)
            break
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:
            return None
        return float(value)
    if isinstance(value, str) and value.strip() != "":
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _xoomar_to_sentiment_pct(score: float) -> float:
    """XOOMAR compositeScore is -1..+1 → map to 0..100 for UI consistency."""
    return max(0.0, min(100.0, (score + 1.0) * 50.0))


class FreeSocialClient:
    """Bulk free social metrics via socialtickers + XOOMAR (no API key required)."""

    name = PROVIDER_NAME

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        cfg = settings.providers_config.get("sentiment") or {}
        st = cfg.get("socialtickers") if isinstance(cfg.get("socialtickers"), dict) else {}
        xo = cfg.get("xoomar") if isinstance(cfg.get("xoomar"), dict) else {}

        self.enabled = bool(cfg.get("enabled", False))
        self.provider_name = str(cfg.get("provider") or PROVIDER_NAME).lower()
        self.st_enabled = bool(st.get("enabled", True))
        self.xo_enabled = bool(xo.get("enabled", True))
        self.st_base = str(
            st.get("base_url") or "https://socialtickers.com"
        ).rstrip("/")
        self.xo_base = str(xo.get("base_url") or "https://xoomar.com").rstrip("/")
        self._timeout = float(
            st.get("timeout_seconds")
            or xo.get("timeout_seconds")
            or cfg.get("timeout_seconds")
            or 15
        )
        self._cache_ttl = float(
            st.get("cache_ttl_seconds") or cfg.get("cache_ttl_seconds") or 300
        )
        self._stale_after = float(
            st.get("stale_after_seconds") or cfg.get("stale_after_seconds") or 900
        )
        self._refresh_seconds = float(
            st.get("refresh_seconds") or cfg.get("refresh_seconds") or self._cache_ttl
        )
        self._st_path = str(
            st.get("leaderboard_path") or "/api/v1/leaderboard"
        )
        self._xo_path = str(
            xo.get("sentiment_path") or "/api/markets/sentiment"
        )

        rl = cfg.get("rate_limit") or {}
        rpm = float(rl.get("requests_per_minute") or 10)
        self.limiter: RateLimiter = rate_limiters.get_or_create(
            self.name,
            capacity=max(rpm, 1),
            refill_per_second=rpm / 60.0,
            max_concurrency=int(rl.get("max_concurrency", 1)),
        )
        self._min_interval = float(rl.get("min_interval_seconds", 3.0))
        self._cooldown_on_429 = float(rl.get("cooldown_on_429_seconds", 120))
        self._cooldown_until = 0.0
        self._last_request_at = 0.0
        self._retry = RetryPolicy(
            max_attempts=3, backoff_base=1.0, backoff_max=30.0, jitter_ratio=0.25
        )
        self._cache = TTLCache()
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._inflight: asyncio.Future | None = None

        self._st_by_symbol: dict[str, dict[str, Any]] = {}
        self._xo_by_symbol: dict[str, dict[str, Any]] = {}
        self._observed_at: datetime | None = None
        self._from_cache = False
        self._sentiment_history: dict[str, list[tuple[datetime, float]]] = {}
        self._last_rate_headers: dict[str, str] = {}
        self.stats = FreeSocialStats()

    @property
    def configured(self) -> bool:
        # Free providers — no API key required
        return bool(
            self.enabled
            and self.provider_name in (PROVIDER_NAME, "socialtickers", "xoomar")
            and (self.st_enabled or self.xo_enabled)
        )

    @property
    def api_key_present(self) -> bool:
        return False  # intentionally keyless

    def in_cooldown(self) -> bool:
        return time.monotonic() < self._cooldown_until

    def cooldown_remaining(self) -> float:
        return max(0.0, self._cooldown_until - time.monotonic())

    async def start(self) -> None:
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout, connect=min(10.0, self._timeout)),
            headers={
                "User-Agent": "CryptoScreener/0.1 (free-social; +https://github.com)",
                "Accept": "application/json",
            },
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def ensure_started(self) -> None:
        if self._client is None:
            await self.start()

    def map_symbol(self, binance_symbol: str) -> SymbolMapping:
        sym = binance_symbol.upper()
        for cand in candidate_bases(sym):
            key = cand.upper()
            if key in self._st_by_symbol or key in self._xo_by_symbol:
                return SymbolMapping(
                    binance_symbol=sym,
                    provider_symbol=key,
                    provider_asset_id=None,
                    mapping_status="MAPPED",
                )
        return SymbolMapping(
            binance_symbol=sym,
            provider_symbol=None,
            provider_asset_id=None,
            mapping_status="UNMAPPED",
        )

    def _status_for_age(self, ts: datetime | None, *, from_cache: bool) -> DataStatus:
        if ts is None:
            return DataStatus.LIVE
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age > self._stale_after:
            return DataStatus.STALE
        if from_cache:
            return DataStatus.CACHED
        return DataStatus.LIVE

    def _fresh(
        self,
        value: float | None,
        *,
        field: str,
        source: str,
        provider_field: str,
        ts: datetime | None,
        from_cache: bool,
        reason: str | None = None,
    ) -> FreshValue[float]:
        if value is None:
            return FreshValue.unavailable(
                source,
                methodology=(
                    reason
                    or f"{field}: provider field '{provider_field}' missing — never fabricated"
                ),
            )
        return FreshValue(
            value=value,
            timestamp=ts or datetime.now(timezone.utc),
            source=source,
            status=self._status_for_age(ts, from_cache=from_cache),
            methodology=(
                f"{field} <- {source}.{provider_field}"
                + (f"; {reason}" if reason else "")
            ),
        )

    def _record_sentiment_history(
        self, binance_symbol: str, ts: datetime, value: float
    ) -> None:
        hist = self._sentiment_history.setdefault(binance_symbol.upper(), [])
        if hist and abs((hist[-1][0] - ts).total_seconds()) < 60 and hist[-1][1] == value:
            return
        hist.append((ts, value))
        if len(hist) > 500:
            self._sentiment_history[binance_symbol.upper()] = hist[-500:]

    def _sentiment_change(
        self, binance_symbol: str, current: float | None, ts: datetime | None
    ) -> FreshValue[float]:
        if current is None or ts is None:
            return FreshValue.unavailable(
                "xoomar",
                methodology=(
                    "sentiment_change: Historical sentiment data unavailable — "
                    "need a prior XOOMAR observation (~24h) — never fabricated"
                ),
            )
        hist = self._sentiment_history.get(binance_symbol.upper()) or []
        older = [(h_ts, h_val) for h_ts, h_val in hist if h_ts < ts]
        if not older:
            return FreshValue.unavailable(
                "xoomar",
                methodology=(
                    "sentiment_change: Historical sentiment data unavailable — "
                    "need a prior XOOMAR observation (~24h)"
                ),
            )
        target = ts.timestamp() - 86400
        window = [
            (h_ts, h_val)
            for h_ts, h_val in older
            if 18 * 3600 <= (ts - h_ts).total_seconds() <= 30 * 3600
        ]
        if not window:
            return FreshValue.unavailable(
                "xoomar",
                methodology=(
                    "sentiment_change: insufficient ~24h historical XOOMAR "
                    "sentiment observations — never fabricated"
                ),
            )
        prior = min(window, key=lambda x: abs(x[0].timestamp() - target))
        change = current - prior[1]
        return FreshValue(
            value=change,
            timestamp=ts,
            source="xoomar",
            status=self._status_for_age(ts, from_cache=self._from_cache),
            methodology=(
                f"sentiment_change = current ({current:.2f}) - prior XOOMAR "
                f"sentiment ({prior[1]:.2f}) at {prior[0].isoformat()} (~24h)"
            ),
        )

    async def refresh_universe(self, *, force: bool = False) -> bool:
        if not self.configured:
            self.stats.last_error = "not_configured"
            self.stats.plan_status = "NOT_CONFIGURED"
            return False
        await self.ensure_started()
        cache_key = "free_social:bulk_v1"
        if not force:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                await provider_health.record_cache(self.name, hit=True)
                self._apply_cached(cached)
                return True
            await provider_health.record_cache(self.name, hit=False)

        async with self._lock:
            if not force:
                cached = await self._cache.get(cache_key)
                if cached is not None:
                    self._apply_cached(cached)
                    return True
            if self._inflight is not None:
                return await self._inflight
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._inflight = fut
            try:
                ok = await self._fetch_both()
                if ok:
                    await self._cache.set(
                        cache_key,
                        {
                            "st": self._st_by_symbol,
                            "xo": self._xo_by_symbol,
                            "observed_at": (
                                self._observed_at.isoformat() if self._observed_at else None
                            ),
                        },
                        self._cache_ttl,
                    )
                fut.set_result(ok)
                return ok
            except Exception as exc:  # noqa: BLE001
                fut.set_exception(exc)
                raise
            finally:
                self._inflight = None

    def _apply_cached(self, cached: dict[str, Any]) -> None:
        self._st_by_symbol = dict(cached.get("st") or {})
        self._xo_by_symbol = dict(cached.get("xo") or {})
        self._from_cache = True
        obs = cached.get("observed_at")
        try:
            self._observed_at = (
                datetime.fromisoformat(obs) if obs else datetime.now(timezone.utc)
            )
        except ValueError:
            self._observed_at = datetime.now(timezone.utc)
        self.stats.socialtickers_received = len(self._st_by_symbol)
        self.stats.xoomar_received = len(self._xo_by_symbol)
        self.stats.assets_received = len(
            set(self._st_by_symbol) | set(self._xo_by_symbol)
        )
        self.stats.connected = self.stats.assets_received > 0
        self.stats.plan_status = "HEALTHY" if self.stats.connected else self.stats.plan_status
        self.stats.last_response_at = _now_iso()

    async def _fetch_both(self) -> bool:
        st_ok = False
        xo_ok = False
        if self.st_enabled:
            st_ok = await self._fetch_socialtickers()
        if self.xo_enabled:
            xo_ok = await self._fetch_xoomar()
        self._from_cache = False
        self._observed_at = datetime.now(timezone.utc)
        self.stats.assets_received = len(
            set(self._st_by_symbol) | set(self._xo_by_symbol)
        )
        self.stats.connected = bool(self.stats.assets_received)
        if self.stats.connected:
            self.stats.plan_status = "HEALTHY"
            self.stats.last_success_at = _now_iso()
            self.stats.provider_generated_at = self._observed_at.isoformat()
            self.stats.last_response_at = _now_iso()
            self.stats.last_error = None
        elif not st_ok and not xo_ok:
            self.stats.plan_status = self.stats.plan_status or "UNAVAILABLE"
        return self.stats.connected

    async def _fetch_socialtickers(self) -> bool:
        data, code, err = await self._do_request(
            self.st_base,
            self._st_path,
            params={"class": "crypto", "sort": "trending", "win": "24h"},
            provider_tag="socialtickers",
        )
        if data is None:
            self.stats.last_error = f"socialtickers: {err or code}"
            return bool(self._st_by_symbol)
        rows = data.get("results") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            self.stats.last_error = "socialtickers: malformed"
            return bool(self._st_by_symbol)
        index: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            ticker = row.get("ticker")
            if not ticker:
                continue
            index[str(ticker).upper()] = row
        self._st_by_symbol = index
        self.stats.socialtickers_received = len(index)
        return bool(index)

    async def _fetch_xoomar(self) -> bool:
        data, code, err = await self._do_request(
            self.xo_base,
            self._xo_path,
            params={"window": "24h"},
            provider_tag="xoomar",
        )
        if data is None:
            self.stats.last_error = f"xoomar: {err or code}"
            return bool(self._xo_by_symbol)
        # Shape: { data: { data: [ ... ] } } or { data: [ ... ] }
        outer = data.get("data") if isinstance(data, dict) else None
        rows = None
        if isinstance(outer, dict):
            rows = outer.get("data")
        elif isinstance(outer, list):
            rows = outer
        if not isinstance(rows, list):
            self.stats.last_error = "xoomar: malformed"
            return bool(self._xo_by_symbol)
        index: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("kind") or "").lower() not in ("crypto", ""):
                # keep crypto; skip equities/fx unless slug matches our base
                if str(row.get("kind") or "").lower() not in ("crypto",):
                    continue
            slug = row.get("slug") or row.get("symbol")
            if not slug:
                continue
            index[str(slug).upper()] = row
        self._xo_by_symbol = index
        self.stats.xoomar_received = len(index)
        return bool(index)

    async def _do_request(
        self,
        base_url: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        provider_tag: str,
    ) -> tuple[dict[str, Any] | None, int | None, str | None]:
        await self.ensure_started()
        assert self._client is not None
        url = f"{base_url.rstrip('/')}{path}"
        last_err: str | None = None
        for attempt in range(self._retry.max_attempts):
            if self.in_cooldown():
                return None, 429, "RATE_LIMITED"
            if self._min_interval > 0:
                wait = self._min_interval - (time.monotonic() - self._last_request_at)
                if wait > 0:
                    await asyncio.sleep(wait)
            await self.limiter.acquire(weight=1.0)
            self._last_request_at = time.monotonic()
            self.stats.requests_total += 1
            start = time.monotonic()
            try:
                resp = await self._client.get(url, params=params)
            except httpx.TimeoutException:
                self.stats.requests_failed += 1
                self.stats.last_failure_at = _now_iso()
                self.stats.last_error = f"{provider_tag}: timeout"
                await provider_health.record_error(self.name, "timeout")
                last_err = "NETWORK_ERROR"
                await asyncio.sleep(self._retry.delay(attempt))
                continue
            except httpx.TransportError as exc:
                self.stats.requests_failed += 1
                self.stats.last_failure_at = _now_iso()
                self.stats.last_error = f"{provider_tag}: {exc.__class__.__name__}"
                await provider_health.record_error(self.name, exc.__class__.__name__)
                last_err = "NETWORK_ERROR"
                await asyncio.sleep(self._retry.delay(attempt))
                continue

            latency_ms = (time.monotonic() - start) * 1000
            self.stats.last_http_status = resp.status_code

            if resp.status_code == 429:
                self.stats.requests_failed += 1
                self.stats.rate_limit_hits += 1
                self.stats.last_failure_at = _now_iso()
                self.stats.plan_status = "RATE_LIMITED"
                await provider_health.record_429(self.name)
                ra = resp.headers.get("Retry-After")
                try:
                    cooldown = float(ra) if ra is not None else self._cooldown_on_429
                except ValueError:
                    cooldown = self._cooldown_on_429
                self._cooldown_until = time.monotonic() + max(cooldown, 1.0)
                await request_audit.log(
                    provider=self.name,
                    endpoint=f"{provider_tag}:{path}",
                    weight=1,
                    status=429,
                    latency_ms=latency_ms,
                    count_429=1,
                )
                return None, 429, "RATE_LIMITED"

            if resp.status_code == 403:
                self.stats.requests_failed += 1
                self.stats.last_failure_at = _now_iso()
                self.stats.plan_status = "PLAN_FORBIDDEN"
                await provider_health.record_error(self.name, "HTTP 403")
                return None, 403, "PLAN_FORBIDDEN"

            if resp.status_code >= 500:
                self.stats.requests_failed += 1
                self.stats.last_failure_at = _now_iso()
                await provider_health.record_error(self.name, f"HTTP {resp.status_code}")
                last_err = "SERVER_ERROR"
                await asyncio.sleep(self._retry.delay(attempt))
                continue

            if resp.status_code >= 400:
                self.stats.requests_failed += 1
                self.stats.last_failure_at = _now_iso()
                await provider_health.record_error(self.name, f"HTTP {resp.status_code}")
                return None, resp.status_code, "HTTP_ERROR"

            try:
                payload = resp.json()
            except Exception:  # noqa: BLE001
                self.stats.requests_failed += 1
                self.stats.last_failure_at = _now_iso()
                await provider_health.record_error(self.name, "malformed_json")
                return None, resp.status_code, "MALFORMED_JSON"

            if not isinstance(payload, dict):
                self.stats.requests_failed += 1
                await provider_health.record_error(self.name, "malformed_json")
                return None, resp.status_code, "MALFORMED_JSON"

            self.stats.requests_success += 1
            await provider_health.record_success(self.name, latency_ms)
            await request_audit.log(
                provider=self.name,
                endpoint=f"{provider_tag}:{path}",
                weight=1,
                status=resp.status_code,
                latency_ms=latency_ms,
            )
            self._last_rate_headers = {
                k: v
                for k, v in resp.headers.items()
                if "rate" in k.lower()
                or k.lower() in ("retry-after", "x-ratelimit-remaining")
            }
            return payload, resp.status_code, None

        return None, None, last_err or "NETWORK_ERROR"

    def metrics_for_symbol(self, binance_symbol: str) -> dict[str, FreshValue]:
        sym = binance_symbol.upper()
        if not self.configured:
            return {
                f: FreshValue.waiting(
                    "sentiment",
                    methodology=(
                        f"{f} requires enabled free sentiment provider "
                        "(socialtickers/xoomar via providers.yaml) — never fabricated"
                    ),
                )
                for f in FIELDS
            }

        has_any = bool(self._st_by_symbol or self._xo_by_symbol)
        if not has_any:
            if self.in_cooldown():
                return {
                    f: FreshValue(
                        value=None,
                        timestamp=None,
                        source=PROVIDER_NAME,
                        status=DataStatus.STALE,
                        methodology=f"{f}: rate-limited (429 cooldown) — STALE",
                    )
                    for f in FIELDS
                }
            return {
                f: FreshValue.waiting(
                    PROVIDER_NAME,
                    methodology=(
                        f"{f}: waiting for free social provider response — never fabricated"
                    ),
                )
                for f in FIELDS
            }

        mapping = self.map_symbol(sym)
        if mapping.mapping_status != "MAPPED" or not mapping.provider_symbol:
            return {
                f: FreshValue.unavailable(
                    PROVIDER_NAME,
                    methodology=(
                        f"{f}: Asset not covered by free social providers "
                        "(socialtickers/XOOMAR)"
                    ),
                )
                for f in FIELDS
            }

        key = mapping.provider_symbol.upper()
        st = self._st_by_symbol.get(key)
        xo = self._xo_by_symbol.get(key)
        ts = self._observed_at
        from_cache = self._from_cache
        out: dict[str, FreshValue] = {}

        for field_name, provider_field in ST_FIELD_MAP.items():
            if st is None:
                out[field_name] = FreshValue.unavailable(
                    "socialtickers",
                    methodology=(
                        f"{field_name}: Asset not on socialtickers leaderboard — "
                        "never fabricated"
                    ),
                )
                continue
            num = _as_number(st.get(provider_field))
            out[field_name] = self._fresh(
                num,
                field=field_name,
                source="socialtickers",
                provider_field=provider_field,
                ts=ts,
                from_cache=from_cache,
            )

        # Sentiment from XOOMAR only (real compositeScore) — never from price
        sent_pct: float | None = None
        if xo is not None:
            raw = _as_number(xo.get("compositeScore"))
            if raw is not None:
                sent_pct = _xoomar_to_sentiment_pct(raw)
                out["sentiment"] = self._fresh(
                    sent_pct,
                    field="sentiment",
                    source="xoomar",
                    provider_field="compositeScore",
                    ts=ts,
                    from_cache=from_cache,
                    reason="mapped (-1..+1) → 0..100",
                )
            else:
                out["sentiment"] = FreshValue.unavailable(
                    "xoomar",
                    methodology="sentiment: compositeScore missing — never fabricated",
                )
        else:
            out["sentiment"] = FreshValue.unavailable(
                "xoomar",
                methodology=(
                    "sentiment: Asset not covered by XOOMAR free set "
                    "(9 crypto majors) — never fabricated"
                ),
            )

        if sent_pct is not None and ts is not None:
            self._record_sentiment_history(sym, ts, sent_pct)
        out["sentiment_change"] = self._sentiment_change(sym, sent_pct, ts)
        return out

    def coverage_for_universe(self, symbols: list[str]) -> dict[str, Any]:
        mapped = 0
        unmapped = 0
        for sym in symbols:
            if self.map_symbol(sym).mapping_status == "MAPPED":
                mapped += 1
            else:
                unmapped += 1
        self.stats.assets_requested = len(symbols)
        self.stats.assets_mapped = mapped
        self.stats.assets_unmapped = unmapped
        return {
            "assets_requested": len(symbols),
            "assets_received": self.stats.assets_received,
            "assets_mapped": mapped,
            "assets_unmapped": unmapped,
            "socialtickers_received": self.stats.socialtickers_received,
            "xoomar_received": self.stats.xoomar_received,
        }

    def diagnostic(self, *, universe_size: int | None = None) -> dict[str, Any]:
        status = "WAITING"
        if not self.enabled:
            status = "DISABLED"
        elif self.stats.plan_status == "RATE_LIMITED" or self.in_cooldown():
            status = "RATE_LIMITED"
        elif self.stats.connected and self.stats.assets_received > 0:
            if self._observed_at and self._status_for_age(
                self._observed_at, from_cache=False
            ) == DataStatus.STALE:
                status = "STALE"
            else:
                status = "LIVE"
        elif self.stats.last_error:
            status = "UNAVAILABLE"

        return {
            "provider": PROVIDER_NAME,
            "vendors": ["socialtickers", "xoomar"],
            "configured": self.configured,
            "enabled": self.enabled,
            "api_key_present": False,
            "api_key_required": False,
            "base_url": {"socialtickers": self.st_base, "xoomar": self.xo_base},
            "endpoint": {
                "socialtickers": self._st_path,
                "xoomar": self._xo_path,
            },
            "connected": self.stats.connected,
            "last_success_at": self.stats.last_success_at,
            "last_failure_at": self.stats.last_failure_at,
            "last_error": self.stats.last_error,
            "requests_total": self.stats.requests_total,
            "requests_success": self.stats.requests_success,
            "requests_failed": self.stats.requests_failed,
            "rate_limit_hits": self.stats.rate_limit_hits,
            "assets_requested": (
                universe_size
                if universe_size is not None
                else self.stats.assets_requested
            ),
            "assets_received": self.stats.assets_received,
            "assets_mapped": self.stats.assets_mapped,
            "assets_unmapped": self.stats.assets_unmapped,
            "socialtickers_received": self.stats.socialtickers_received,
            "xoomar_received": self.stats.xoomar_received,
            "last_response_at": self.stats.last_response_at,
            "provider_generated_at": self.stats.provider_generated_at,
            "cache_ttl_seconds": self._cache_ttl,
            "stale_after_seconds": self._stale_after,
            "refresh_seconds": self._refresh_seconds,
            "cooldown": self.in_cooldown(),
            "plan_status": self.stats.plan_status,
            "last_http_status": self.stats.last_http_status,
            "rate_limit_headers": self._last_rate_headers,
            "status": status,
            "cache_stats": self._cache.stats(),
            "note": (
                "Free providers: socialtickers (share/mentions/upvotes) + "
                "XOOMAR (sentiment for 9 crypto majors). Uncovered assets stay UNAVAILABLE."
            ),
        }

    def raw_asset(self, provider_symbol: str) -> dict[str, Any] | None:
        key = provider_symbol.upper()
        return self._st_by_symbol.get(key) or self._xo_by_symbol.get(key)

    def payload_hash(self, asset: dict[str, Any] | None) -> str | None:
        if not asset:
            return None
        import hashlib
        import json

        blob = json.dumps(
            {k: asset.get(k) for k in ("ticker", "share", "mentions", "upvotes", "slug", "compositeScore")},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    @property
    def refresh_seconds(self) -> float:
        return self._refresh_seconds


_shared: FreeSocialClient | None = None


def get_free_social_client(settings: Settings | None = None) -> FreeSocialClient:
    global _shared
    if _shared is None:
        from app.config import get_settings

        _shared = FreeSocialClient(settings or get_settings())
    return _shared


def reset_free_social_client_for_tests() -> None:
    global _shared
    _shared = None
