"""Real LunarCrush API v4 client. Never fabricates social metrics."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass
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

logger = get_logger("providers.lunarcrush")

PROVIDER_NAME = "lunarcrush"
COINS_LIST_PATH = "/public/coins/list/v2"
KNOWN_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USD")

# Canonical product fields <- LunarCrush response fields
FIELD_MAP: dict[str, str] = {
    "social_dominance": "social_dominance",
    "social_volume": "social_volume_24h",
    # LunarCrush docs/SDK treat social_volume_24h as social mention/post volume.
    "mentions": "social_volume_24h",
    "engagement": "interactions_24h",
    "sentiment": "sentiment",
}

FIELDS = (
    "social_dominance",
    "social_volume",
    "mentions",
    "engagement",
    "sentiment",
    "sentiment_change",
)


@dataclass
class SymbolMapping:
    binance_symbol: str
    provider_symbol: str | None
    provider_asset_id: int | str | None
    mapping_status: str  # MAPPED | UNMAPPED | AMBIGUOUS


@dataclass
class LunarCrushStats:
    requests_total: int = 0
    requests_success: int = 0
    requests_failed: int = 0
    rate_limit_hits: int = 0
    assets_received: int = 0
    assets_mapped: int = 0
    assets_unmapped: int = 0
    assets_requested: int = 0
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_response_at: str | None = None
    last_error: str | None = None
    last_http_status: int | None = None
    connected: bool = False
    plan_status: str | None = None  # AUTH_FAILED | PLAN_FORBIDDEN | RATE_LIMITED | ...
    provider_generated_at: str | None = None


def candidate_bases(binance_symbol: str) -> list[str]:
    """Generate candidate provider symbols without inventing aliases beyond quote strip."""
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


def _parse_generated(raw: Any) -> datetime | None:
    if raw is None:
        return None
    try:
        if isinstance(raw, (int, float)):
            # LunarCrush uses unix seconds
            ts = float(raw)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        if isinstance(raw, str) and raw.strip():
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError, OSError):
        return None
    return None


def _as_number(value: Any) -> float | None:
    """Return numeric value when present; distinguish missing from legitimate zero."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:  # NaN
            return None
        return float(value)
    if isinstance(value, str) and value.strip() != "":
        try:
            return float(value)
        except ValueError:
            return None
    return None


class LunarCrushClient:
    """Bulk-fetch LunarCrush coins/list/v2 with rate limits, cache, and honest status."""

    name = PROVIDER_NAME

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        cfg = settings.providers_config.get("sentiment") or {}
        lc = cfg.get("lunarcrush") if isinstance(cfg.get("lunarcrush"), dict) else {}
        self.enabled = bool(cfg.get("enabled", False)) and bool(lc.get("enabled", True))
        self.provider_name = str(cfg.get("provider") or PROVIDER_NAME).lower()
        self.base_url = str(
            (lc.get("base_url") if lc.get("base_url") else None)
            or cfg.get("base_url")
            or "https://lunarcrush.com/api4"
        ).rstrip("/")
        env_key = str(cfg.get("api_key_env") or "LUNARCRUSH_API_KEY")
        # Prefer configured env; also accept legacy SENTIMENT_API_KEY
        self.api_key = str(
            os.getenv(env_key)
            or os.getenv("LUNARCRUSH_API_KEY")
            or os.getenv("SENTIMENT_API_KEY")
            or cfg.get("api_key")
            or ""
        )
        self._timeout = float(
            lc.get("timeout_seconds") or cfg.get("timeout_seconds") or 10
        )
        self._cache_ttl = float(
            lc.get("cache_ttl_seconds") or cfg.get("cache_ttl_seconds") or 300
        )
        self._stale_after = float(
            lc.get("stale_after_seconds") or cfg.get("stale_after_seconds") or 900
        )
        self._refresh_seconds = float(
            lc.get("refresh_seconds") or cfg.get("refresh_seconds") or self._cache_ttl
        )
        self._page_limit = int(lc.get("page_limit") or 1000)
        self._max_pages = int(lc.get("max_pages") or 10)
        self._coins_list_path = str(lc.get("coins_list_path") or COINS_LIST_PATH)
        self._field_ttl: dict[str, float] = dict(cfg.get("field_ttl_seconds") or {})

        rl = cfg.get("rate_limit") or {}
        rpm = float(
            lc.get("max_requests_per_minute")
            or rl.get("requests_per_minute")
            or 10
        )
        self.limiter: RateLimiter = rate_limiters.get_or_create(
            self.name,
            capacity=max(rpm, 1),
            refill_per_second=rpm / 60.0,
            max_concurrency=int(rl.get("max_concurrency", 1)),
        )
        self._min_interval = float(rl.get("min_interval_seconds", 6.0))
        self._cooldown_on_429 = float(rl.get("cooldown_on_429_seconds", 120))
        self._cooldown_until = 0.0
        self._last_request_at = 0.0
        self._retry = RetryPolicy(max_attempts=3, backoff_base=1.0, backoff_max=60.0, jitter_ratio=0.25)
        self._cache = TTLCache()
        self._client: httpx.AsyncClient | None = None
        self._inflight: asyncio.Future | None = None
        self._lock = asyncio.Lock()

        # symbol(upper) -> asset dict
        self._by_symbol: dict[str, dict[str, Any]] = {}
        self._assets_list: list[dict[str, Any]] = []
        self._observed_at: datetime | None = None
        self._provider_generated_at: datetime | None = None
        self._from_cache = False
        # binance_symbol -> prior sentiment observations for change calc
        self._sentiment_history: dict[str, list[tuple[datetime, float]]] = {}
        self._last_rate_headers: dict[str, str] = {}
        self.stats = LunarCrushStats()

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and self.provider_name == PROVIDER_NAME
            and self.api_key
            and self.base_url
        )

    @property
    def api_key_present(self) -> bool:
        return bool(self.api_key)

    def in_cooldown(self) -> bool:
        return time.monotonic() < self._cooldown_until

    def cooldown_remaining(self) -> float:
        return max(0.0, self._cooldown_until - time.monotonic())

    async def start(self) -> None:
        if self._client is not None:
            return
        headers = {"User-Agent": "CryptoScreener/0.1", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout, connect=min(10.0, self._timeout)),
            headers=headers,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def ensure_started(self) -> None:
        if self._client is None:
            await self.start()

    def map_symbol(self, binance_symbol: str) -> SymbolMapping:
        """Map Binance symbol using LunarCrush symbols as authority — never invent."""
        sym = binance_symbol.upper()
        for cand in candidate_bases(sym):
            asset = self._by_symbol.get(cand.upper())
            if asset is None:
                continue
            provider_sym = str(asset.get("symbol") or cand).upper()
            return SymbolMapping(
                binance_symbol=sym,
                provider_symbol=provider_sym,
                provider_asset_id=asset.get("id"),
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
        provider_field: str,
        ts: datetime | None,
        from_cache: bool,
        reason: str | None = None,
    ) -> FreshValue[float]:
        source = PROVIDER_NAME
        if value is None:
            return FreshValue.unavailable(
                source,
                methodology=(
                    reason
                    or f"{field}: provider field '{provider_field}' missing — never fabricated"
                ),
            )
        status = self._status_for_age(ts, from_cache=from_cache)
        return FreshValue(
            value=value,
            timestamp=ts or datetime.now(timezone.utc),
            source=source,
            status=status,
            methodology=(
                f"{field} <- lunarcrush.{provider_field}"
                + (f"; {reason}" if reason else "")
            ),
        )

    def _record_sentiment_history(self, binance_symbol: str, ts: datetime, value: float) -> None:
        hist = self._sentiment_history.setdefault(binance_symbol.upper(), [])
        # Avoid duplicate points within 60s
        if hist and abs((hist[-1][0] - ts).total_seconds()) < 60 and hist[-1][1] == value:
            return
        hist.append((ts, value))
        # Keep ~7 days of 5-min samples max ~2016; trim harder
        if len(hist) > 500:
            self._sentiment_history[binance_symbol.upper()] = hist[-500:]

    def _sentiment_change(
        self, binance_symbol: str, current: float | None, ts: datetime | None
    ) -> FreshValue[float]:
        if current is None or ts is None:
            return FreshValue.unavailable(
                PROVIDER_NAME,
                methodology=(
                    "sentiment_change: Historical sentiment data unavailable from "
                    "configured LunarCrush plan / observations — never fabricated"
                ),
            )
        hist = self._sentiment_history.get(binance_symbol.upper()) or []
        # Only strictly older observations may serve as the prior baseline.
        older = [(h_ts, h_val) for h_ts, h_val in hist if h_ts < ts]
        if not older:
            return FreshValue.unavailable(
                PROVIDER_NAME,
                methodology=(
                    "sentiment_change: Historical sentiment data unavailable — "
                    "need a prior LunarCrush sentiment observation (~24h)"
                ),
            )
        # Prefer observation closest to 24h ago within [18h, 30h]
        target = ts.timestamp() - 86400
        window = [
            (h_ts, h_val)
            for h_ts, h_val in older
            if 18 * 3600 <= (ts - h_ts).total_seconds() <= 30 * 3600
        ]
        prior: tuple[datetime, float] | None = None
        if window:
            prior = min(window, key=lambda x: abs(x[0].timestamp() - target))
        else:
            # Have older points but none in the ~24h window — do not invent a change
            return FreshValue.unavailable(
                PROVIDER_NAME,
                methodology=(
                    "sentiment_change: insufficient ~24h historical LunarCrush "
                    "sentiment observations — never fabricated"
                ),
            )
        change = current - prior[1]
        status = self._status_for_age(ts, from_cache=self._from_cache)
        return FreshValue(
            value=change,
            timestamp=ts,
            source=PROVIDER_NAME,
            status=status,
            methodology=(
                f"sentiment_change = current sentiment ({current}) - prior LunarCrush "
                f"sentiment ({prior[1]}) at {prior[0].isoformat()} (~24h window)"
            ),
        )

    async def refresh_universe(self, *, force: bool = False) -> bool:
        """Fetch bulk coins list (paginated). Returns True on usable data."""
        if not self.configured:
            self.stats.last_error = "not_configured"
            self.stats.plan_status = "NOT_CONFIGURED"
            return False
        await self.ensure_started()

        cache_key = "lunarcrush:coins_list_v2"
        if not force:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                await provider_health.record_cache(self.name, hit=True)
                self._apply_bulk(cached["assets"], cached.get("generated"), from_cache=True)
                return True
            await provider_health.record_cache(self.name, hit=False)

        async with self._lock:
            # Double-check cache inside lock
            if not force:
                cached = await self._cache.get(cache_key)
                if cached is not None:
                    self._apply_bulk(cached["assets"], cached.get("generated"), from_cache=True)
                    return True
            if self._inflight is not None:
                return await self._inflight

            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._inflight = fut
            try:
                ok = await self._fetch_all_pages()
                if ok:
                    await self._cache.set(
                        cache_key,
                        {
                            "assets": self._assets_list,
                            "generated": (
                                self._provider_generated_at.isoformat()
                                if self._provider_generated_at
                                else None
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

    async def _fetch_all_pages(self) -> bool:
        if self.in_cooldown():
            self.stats.last_error = f"cooldown ({self.cooldown_remaining():.0f}s)"
            self.stats.plan_status = "RATE_LIMITED"
            # Serve last good data if any
            return bool(self._assets_list)

        all_assets: list[dict[str, Any]] = []
        generated: Any = None
        page = 0
        while page < self._max_pages:
            data, http_status, err_code = await self._do_request(
                self._coins_list_path,
                params={"limit": self._page_limit, "page": page, "sort": "market_cap_rank"},
            )
            if data is None:
                if page == 0:
                    self.stats.connected = False
                    self.stats.plan_status = err_code or "NETWORK_ERROR"
                    # Keep prior assets if we had them (stale path)
                    return bool(self._assets_list)
                break
            cfg = data.get("config") if isinstance(data, dict) else None
            if isinstance(cfg, dict) and generated is None:
                generated = cfg.get("generated")
            rows = data.get("data") if isinstance(data, dict) else None
            if not isinstance(rows, list) or not rows:
                break
            all_assets.extend([r for r in rows if isinstance(r, dict)])
            total_rows = 0
            if isinstance(cfg, dict):
                try:
                    total_rows = int(cfg.get("total_rows") or 0)
                except (TypeError, ValueError):
                    total_rows = 0
            if len(rows) < self._page_limit:
                break
            if total_rows and len(all_assets) >= total_rows:
                break
            page += 1

        if not all_assets:
            self.stats.last_error = "empty_response"
            self.stats.plan_status = self.stats.plan_status or "EMPTY"
            return bool(self._assets_list)

        self._apply_bulk(all_assets, generated, from_cache=False)
        self.stats.connected = True
        self.stats.plan_status = "HEALTHY"
        self.stats.last_http_status = 200
        return True

    def _apply_bulk(
        self, assets: list[dict[str, Any]], generated: Any, *, from_cache: bool
    ) -> None:
        index: dict[str, dict[str, Any]] = {}
        for asset in assets:
            sym = asset.get("symbol")
            if not sym:
                continue
            index[str(sym).upper()] = asset
        self._by_symbol = index
        self._assets_list = list(assets)
        self._from_cache = from_cache
        self._observed_at = datetime.now(timezone.utc)
        self._provider_generated_at = _parse_generated(generated) or self._observed_at
        self.stats.assets_received = len(index)
        self.stats.last_response_at = _now_iso()
        if assets:
            self.stats.connected = True
            if not self.stats.plan_status or self.stats.plan_status in (
                "EMPTY",
                "NOT_CONFIGURED",
                "NETWORK_ERROR",
            ):
                self.stats.plan_status = "HEALTHY"
        if self._provider_generated_at:
            self.stats.provider_generated_at = self._provider_generated_at.isoformat()

    async def _do_request(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, int | None, str | None]:
        if self._client is None:
            await self.ensure_started()
        assert self._client is not None

        url = f"{self.base_url}{path}"
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
            except httpx.TimeoutException as exc:
                self.stats.requests_failed += 1
                self.stats.last_failure_at = _now_iso()
                self.stats.last_error = f"timeout: {exc.__class__.__name__}"
                await provider_health.record_error(self.name, "timeout")
                last_err = "NETWORK_ERROR"
                await asyncio.sleep(self._retry.delay(attempt))
                continue
            except httpx.TransportError as exc:
                self.stats.requests_failed += 1
                self.stats.last_failure_at = _now_iso()
                self.stats.last_error = f"transport: {exc.__class__.__name__}"
                await provider_health.record_error(self.name, str(exc.__class__.__name__))
                last_err = "NETWORK_ERROR"
                await asyncio.sleep(self._retry.delay(attempt))
                continue

            latency_ms = (time.monotonic() - start) * 1000
            self.stats.last_http_status = resp.status_code

            if resp.status_code == 401:
                self.stats.requests_failed += 1
                self.stats.last_failure_at = _now_iso()
                self.stats.last_error = "HTTP 401 AUTH_FAILED"
                self.stats.plan_status = "AUTH_FAILED"
                await provider_health.record_error(self.name, "HTTP 401")
                await request_audit.log(
                    provider=self.name, endpoint=path, weight=1, status=401, latency_ms=latency_ms
                )
                return None, 401, "AUTH_FAILED"

            if resp.status_code == 403:
                self.stats.requests_failed += 1
                self.stats.last_failure_at = _now_iso()
                self.stats.last_error = "HTTP 403 PLAN_FORBIDDEN"
                self.stats.plan_status = "PLAN_FORBIDDEN"
                await provider_health.record_error(self.name, "HTTP 403")
                await request_audit.log(
                    provider=self.name, endpoint=path, weight=1, status=403, latency_ms=latency_ms
                )
                return None, 403, "PLAN_FORBIDDEN"

            if resp.status_code == 404:
                self.stats.requests_failed += 1
                self.stats.last_failure_at = _now_iso()
                self.stats.last_error = "HTTP 404"
                await provider_health.record_error(self.name, "HTTP 404")
                await request_audit.log(
                    provider=self.name, endpoint=path, weight=1, status=404, latency_ms=latency_ms
                )
                return None, 404, "NOT_FOUND"

            if resp.status_code == 429:
                self.stats.requests_failed += 1
                self.stats.rate_limit_hits += 1
                self.stats.last_failure_at = _now_iso()
                self.stats.last_error = "HTTP 429"
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
                    endpoint=path,
                    weight=1,
                    status=429,
                    latency_ms=latency_ms,
                    count_429=1,
                )
                return None, 429, "RATE_LIMITED"

            if resp.status_code >= 500:
                self.stats.requests_failed += 1
                self.stats.last_failure_at = _now_iso()
                self.stats.last_error = f"HTTP {resp.status_code}"
                await provider_health.record_error(self.name, f"HTTP {resp.status_code}")
                await request_audit.log(
                    provider=self.name,
                    endpoint=path,
                    weight=1,
                    status=resp.status_code,
                    latency_ms=latency_ms,
                )
                last_err = "SERVER_ERROR"
                await asyncio.sleep(self._retry.delay(attempt))
                continue

            if resp.status_code >= 400:
                self.stats.requests_failed += 1
                self.stats.last_failure_at = _now_iso()
                self.stats.last_error = f"HTTP {resp.status_code}"
                await provider_health.record_error(self.name, f"HTTP {resp.status_code}")
                return None, resp.status_code, "HTTP_ERROR"

            try:
                payload = resp.json()
            except Exception as exc:  # noqa: BLE001
                self.stats.requests_failed += 1
                self.stats.last_failure_at = _now_iso()
                self.stats.last_error = f"malformed_json: {exc.__class__.__name__}"
                await provider_health.record_error(self.name, "malformed_json")
                return None, resp.status_code, "MALFORMED_JSON"

            if not isinstance(payload, dict):
                self.stats.requests_failed += 1
                self.stats.last_failure_at = _now_iso()
                self.stats.last_error = "malformed_json: not_object"
                await provider_health.record_error(self.name, "malformed_json")
                return None, resp.status_code, "MALFORMED_JSON"

            self.stats.requests_success += 1
            self.stats.last_success_at = _now_iso()
            self.stats.last_error = None
            await provider_health.record_success(self.name, latency_ms)
            await request_audit.log(
                provider=self.name,
                endpoint=path,
                weight=1,
                status=resp.status_code,
                latency_ms=latency_ms,
            )
            # Capture rate-limit headers without logging secrets
            self._last_rate_headers = {
                k: v
                for k, v in resp.headers.items()
                if "rate" in k.lower()
                or k.lower()
                in ("retry-after", "x-ratelimit-remaining", "x-ratelimit-limit")
            }
            return payload, resp.status_code, None

        return None, None, last_err or "NETWORK_ERROR"

    def metrics_for_symbol(self, binance_symbol: str) -> dict[str, FreshValue]:
        """Map one Binance symbol to product FreshValues. Never fabricates."""
        sym = binance_symbol.upper()
        if not self.configured:
            return {
                f: FreshValue.waiting(
                    "sentiment",
                    methodology=(
                        f"{f} requires configured LunarCrush provider "
                        "(providers.yaml + LUNARCRUSH_API_KEY) — never fabricated"
                    ),
                )
                for f in FIELDS
            }

        if self.stats.plan_status in ("AUTH_FAILED", "PLAN_FORBIDDEN"):
            reason = (
                "LunarCrush authentication failed"
                if self.stats.plan_status == "AUTH_FAILED"
                else "LunarCrush social endpoint forbidden for configured plan (PLAN_FORBIDDEN)"
            )
            return {
                f: FreshValue.unavailable(PROVIDER_NAME, methodology=f"{f}: {reason}")
                for f in FIELDS
            }

        if not self._by_symbol:
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
                    methodology=f"{f}: waiting for LunarCrush bulk response — never fabricated",
                )
                for f in FIELDS
            }

        mapping = self.map_symbol(sym)
        if mapping.mapping_status != "MAPPED" or not mapping.provider_symbol:
            return {
                f: FreshValue.unavailable(
                    PROVIDER_NAME,
                    methodology=f"{f}: Asset not covered by LunarCrush",
                )
                for f in FIELDS
            }

        asset = self._by_symbol.get(mapping.provider_symbol.upper())
        if asset is None:
            return {
                f: FreshValue.unavailable(
                    PROVIDER_NAME,
                    methodology=f"{f}: Asset not covered by LunarCrush",
                )
                for f in FIELDS
            }

        ts = self._provider_generated_at or self._observed_at
        from_cache = self._from_cache
        out: dict[str, FreshValue] = {}
        for field_name, provider_field in FIELD_MAP.items():
            raw = asset.get(provider_field)
            num = _as_number(raw)
            out[field_name] = self._fresh(
                num,
                field=field_name,
                provider_field=provider_field,
                ts=ts,
                from_cache=from_cache,
            )

        sent_val = _as_number(asset.get("sentiment"))
        if sent_val is not None and ts is not None:
            self._record_sentiment_history(sym, ts, sent_val)
        out["sentiment_change"] = self._sentiment_change(sym, sent_val, ts)
        return out

    def coverage_for_universe(self, symbols: list[str]) -> dict[str, Any]:
        mapped = 0
        unmapped = 0
        for sym in symbols:
            m = self.map_symbol(sym)
            if m.mapping_status == "MAPPED":
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
        }

    def diagnostic(self, *, universe_size: int | None = None) -> dict[str, Any]:
        status = "WAITING"
        if not self.enabled:
            status = "DISABLED"
        elif not self.api_key_present:
            status = "WAITING"
        elif self.stats.plan_status == "AUTH_FAILED":
            status = "AUTH_FAILED"
        elif self.stats.plan_status == "PLAN_FORBIDDEN":
            status = "PLAN_FORBIDDEN"
        elif self.stats.plan_status == "RATE_LIMITED" or self.in_cooldown():
            status = "RATE_LIMITED"
        elif self.stats.connected and self.stats.assets_received > 0:
            if self._observed_at and self._status_for_age(self._observed_at, from_cache=False) == DataStatus.STALE:
                status = "STALE"
            else:
                status = "LIVE"
        elif self.stats.last_error:
            status = "UNAVAILABLE"

        payload = {
            "provider": PROVIDER_NAME,
            "configured": self.configured,
            "enabled": self.enabled,
            "api_key_present": self.api_key_present,
            "base_url": self.base_url,
            "endpoint": self._coins_list_path,
            "connected": self.stats.connected,
            "last_success_at": self.stats.last_success_at,
            "last_failure_at": self.stats.last_failure_at,
            "last_error": self.stats.last_error,
            "requests_total": self.stats.requests_total,
            "requests_success": self.stats.requests_success,
            "requests_failed": self.stats.requests_failed,
            "rate_limit_hits": self.stats.rate_limit_hits,
            "assets_requested": universe_size if universe_size is not None else self.stats.assets_requested,
            "assets_received": self.stats.assets_received,
            "assets_mapped": self.stats.assets_mapped,
            "assets_unmapped": self.stats.assets_unmapped,
            "last_response_at": self.stats.last_response_at,
            "provider_generated_at": self.stats.provider_generated_at,
            "cache_ttl_seconds": self._cache_ttl,
            "stale_after_seconds": self._stale_after,
            "refresh_seconds": self._refresh_seconds,
            "cooldown": self.in_cooldown(),
            "plan_status": self.stats.plan_status,
            "last_http_status": self.stats.last_http_status,
            "rate_limit_headers": getattr(self, "_last_rate_headers", {}) or {},
            "status": status,
            "cache_stats": self._cache.stats(),
        }
        return payload

    def raw_asset(self, provider_symbol: str) -> dict[str, Any] | None:
        return self._by_symbol.get(provider_symbol.upper())

    @property
    def refresh_seconds(self) -> float:
        return self._refresh_seconds

    def payload_hash(self, asset: dict[str, Any]) -> str:
        keys = sorted(set(FIELD_MAP.values()) | {"sentiment", "id", "symbol"})
        blob = json.dumps(
            {k: asset.get(k) for k in keys},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:32]


_shared_client: LunarCrushClient | None = None


def get_lunarcrush_client(settings: Settings | None = None) -> LunarCrushClient:
    global _shared_client
    if _shared_client is None:
        from app.config import get_settings

        _shared_client = LunarCrushClient(settings or get_settings())
    return _shared_client


def reset_lunarcrush_client_for_tests() -> None:
    global _shared_client
    _shared_client = None
