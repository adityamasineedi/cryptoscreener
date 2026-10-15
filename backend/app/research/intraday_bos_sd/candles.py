"""Candle time, resampling, ATR, and data-quality checks.

Bar identity is the UTC open time. A bar is usable only after it has closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.engines.mtf.indicators import atr_series

TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}


def ensure_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return ensure_utc(value)
    text = str(value).replace("Z", "+00:00")
    return ensure_utc(datetime.fromisoformat(text))


def tf_delta(timeframe: str) -> timedelta:
    ms = TF_MS.get(timeframe)
    if ms is None:
        raise ValueError(f"unsupported timeframe {timeframe}")
    return timedelta(milliseconds=ms)


@dataclass(frozen=True)
class Bar:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float | None = None

    @property
    def close_time(self) -> datetime:
        raise RuntimeError("close_time needs the series timeframe")


@dataclass
class Series:
    timeframe: str
    bars: list[Bar]

    def __post_init__(self) -> None:
        self.delta = tf_delta(self.timeframe)
        self.highs = [b.high for b in self.bars]
        self.lows = [b.low for b in self.bars]
        self.closes = [b.close for b in self.bars]
        self.opens = [b.open for b in self.bars]
        self.volumes = [b.volume for b in self.bars]
        self.atr = atr_series(self.highs, self.lows, self.closes, 14)

    def __len__(self) -> int:
        return len(self.bars)

    def close_time(self, index: int) -> datetime:
        return self.bars[index].open_time + self.delta

    def preceding_atr(self, index: int) -> float | None:
        if index <= 0:
            return None
        return self.atr[index - 1]


def series_from_rows(timeframe: str, rows: Sequence[Mapping[str, Any]]) -> Series:
    bars: list[Bar] = []
    for row in rows:
        qv = row.get("quote_volume")
        bars.append(
            Bar(
                open_time=parse_time(row.get("time") or row.get("open_time")),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume") or 0.0),
                quote_volume=None if qv is None else float(qv),
            )
        )
    bars.sort(key=lambda b: b.open_time)
    return Series(timeframe, bars)


def drop_incomplete_tail(series: Series, now: datetime) -> Series:
    if not series.bars:
        return series
    now_u = ensure_utc(now)
    kept = [b for b in series.bars if b.open_time + series.delta <= now_u]
    return Series(series.timeframe, kept)


@dataclass
class QualityReport:
    ok: bool
    errors: list[str]
    gap_fraction: float
    bars: int
    start: datetime | None
    end: datetime | None


def validate_series(series: Series) -> QualityReport:
    errors: list[str] = []
    bars = series.bars
    if not bars:
        return QualityReport(False, ["empty"], 1.0, 0, None, None)
    seen: set[datetime] = set()
    gaps = 0
    expected = 0
    for i, bar in enumerate(bars):
        if bar.open_time in seen:
            errors.append(f"duplicate:{bar.open_time.isoformat()}")
        seen.add(bar.open_time)
        if min(bar.open, bar.high, bar.low, bar.close) <= 0:
            errors.append(f"non_positive:{bar.open_time.isoformat()}")
        if bar.high < max(bar.open, bar.close, bar.low):
            errors.append(f"high_inconsistent:{bar.open_time.isoformat()}")
        if bar.low > min(bar.open, bar.close, bar.high):
            errors.append(f"low_inconsistent:{bar.open_time.isoformat()}")
        if bar.volume < 0:
            errors.append(f"negative_volume:{bar.open_time.isoformat()}")
        if i:
            delta = bar.open_time - bars[i - 1].open_time
            if delta <= timedelta(0):
                errors.append(f"time_not_increasing:{bar.open_time.isoformat()}")
            elif delta != series.delta:
                missing = int(delta / series.delta) - 1
                if missing > 0:
                    gaps += missing
                    expected += missing
            expected += 1
    gap_fraction = (gaps / expected) if expected else 0.0
    if gap_fraction > 0.02:
        errors.append(f"gap_fraction:{gap_fraction:.4f}")
    return QualityReport(
        ok=not errors,
        errors=errors,
        gap_fraction=gap_fraction,
        bars=len(bars),
        start=bars[0].open_time,
        end=bars[-1].open_time,
    )


def resample_complete(source: Series, timeframe: str) -> Series:
    """Build higher-timeframe bars only when every child bar is present."""
    dest_delta = tf_delta(timeframe)
    if dest_delta < source.delta or dest_delta % source.delta != timedelta(0):
        raise ValueError("destination must be an integer multiple of source")
    need = int(dest_delta / source.delta)
    buckets: dict[datetime, list[Bar]] = {}
    for bar in source.bars:
        bucket = bar.open_time - timedelta(
            seconds=bar.open_time.timestamp() % dest_delta.total_seconds()
        )
        # Floor in UTC using integer ms to avoid float drift.
        ms = int(bar.open_time.timestamp() * 1000)
        step = int(dest_delta.total_seconds() * 1000)
        bucket = datetime.fromtimestamp((ms // step) * step / 1000, tz=timezone.utc)
        buckets.setdefault(bucket, []).append(bar)
    out: list[Bar] = []
    for open_time in sorted(buckets):
        children = buckets[open_time]
        if len(children) != need:
            continue
        expected = [open_time + source.delta * i for i in range(need)]
        if [c.open_time for c in children] != expected:
            continue
        qvs = [c.quote_volume for c in children]
        quote = None if any(v is None for v in qvs) else float(sum(v or 0.0 for v in qvs))
        out.append(
            Bar(
                open_time=open_time,
                open=children[0].open,
                high=max(c.high for c in children),
                low=min(c.low for c in children),
                close=children[-1].close,
                volume=sum(c.volume for c in children),
                quote_volume=quote,
            )
        )
    return Series(timeframe, out)
