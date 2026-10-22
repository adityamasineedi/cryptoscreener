"""Alert sidecar. Reads live data read-only; writes only under ``--out-dir`` (default ``<repo>/data/alerts``).

  python -m app.alerts.runner watch    --live-root D:/cryptoscreener [--hours 24] [--once]
  python -m app.alerts.runner backfill --live-root D:/cryptoscreener [--hours 24]

``watch`` follows new decision-journal rows (and the FWD-1 / FWD-3 records) from the moment it starts.
``backfill`` replays the last ``--hours`` of real rows once, in dry run, judging LATE against the time each
row was recorded by live (as if the sidecar had been running then) and following each alert on the closed
bars up to now. Neither mode sends anything unless the config has ``enabled: true`` and ``dry_run: false``
(backfill never sends).
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.alerts import sources as src
from app.alerts.config import AlertConfig, load_config
from app.alerts.data import MarketData, PostgresReadOnly, read_env_values
from app.alerts.fwd_ledgers import ForwardLedgers
from app.alerts.pipeline import AlertPipeline, all_summary_alerts, load_state, merge_stats, save_state
from app.alerts.plan import utc
from app.alerts.sender import ReplyingTelegramSender, is_private_chat_id

REPO_ROOT = Path(__file__).resolve().parents[3]

for _noisy in ("httpx", "httpcore"):
    # The Telegram URL carries the bot token.
    logging.getLogger(_noisy).setLevel(logging.WARNING)


def _say(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat()} {msg}", flush=True)


def _paths(cfg: AlertConfig, live_root: Path) -> dict[str, Path]:
    s = cfg.raw["sources"]

    def under(p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else live_root / q

    return {
        "journal": under(s["journal"]),
        "fwd1": under(s["book16_fwd1_ledger"]),
        "fwd3": under(s["lcsc_fwd3_runs"]),
    }


def _sender(cfg: AlertConfig, env: dict[str, str]) -> ReplyingTelegramSender | None:
    if not cfg.sends_telegram:
        return None
    tg = cfg.raw["telegram"]
    token, chat = env.get(tg["bot_token_env"]), env.get(tg["chat_id_env"])
    if not token or not chat:
        _say(f"telegram delivery requested but {tg['bot_token_env']} / {tg['chat_id_env']} not set: dry run")
        return None
    if not is_private_chat_id(chat):
        # Owner ruling D117: private chat only. The id itself is never printed.
        _say(f"telegram delivery requested but {tg['chat_id_env']} is not a private chat id "
             "(a group, channel or @name): dry run")
        return None
    return ReplyingTelegramSender(bot_token=token, chat_id=chat, timeout=float(tg["timeout_seconds"]))


def _env(cfg: AlertConfig, live_root: Path, env_file: Path | None) -> dict[str, str]:
    tg = cfg.raw["telegram"]
    return read_env_values(env_file or live_root / ".env", ["DATABASE_URL", tg["bot_token_env"], tg["chat_id_env"]])


# --- watch -------------------------------------------------------------------------------------------
def poll_once(pipe: AlertPipeline, paths: dict[str, Path], now: datetime) -> dict[str, int]:
    st = pipe.state
    off = st["offsets"]
    counts: collections.Counter[str] = collections.Counter()
    lines, new_off = src.read_lines(paths["journal"], int(off["journal_offset"]))
    if new_off < int(off["journal_offset"]):  # never shrinks; a replaced file restarts at its end
        new_off = int(off["journal_offset"])
    first = int(off["journal_lines"]) + 1
    # Rows deferred on an earlier poll (their paper position or state file was not written yet), then new rows.
    batch = [(int(d["line"]), str(d["raw"])) for d in st.get("deferred", [])]
    batch += [(first + i, raw) for i, raw in lines]
    st["deferred"] = []
    defer_s = float(pipe.cfg.raw["defer_missing_engine_values_seconds"])
    for line_no, raw in batch:
        for item in src.journal_items([(0, raw)], first_line_no=line_no, data=pipe.data, cfg=pipe.cfg):
            if src.awaiting_engine_values(item) and (now - item.decided_at).total_seconds() < defer_s:
                st["deferred"].append({"line": line_no, "raw": raw})
                counts["deferred"] += 1
                continue
            counts[pipe.handle(item, now)["code"]] += 1
    off["journal_offset"] = new_off
    off["journal_lines"] = int(off["journal_lines"]) + len(lines)

    seen1 = set(st["fwd1_seen"])
    sigs, off["fwd1_seq"] = src.fwd1_items(paths["fwd1"], after_seq=int(off["fwd1_seq"]), data=pipe.data, cfg=pipe.cfg, seen=seen1)
    st["fwd1_seen"] = sorted(seen1)
    for s in sigs:
        counts[pipe.handle(s, now)["code"]] += 1
    seen3 = set(st["fwd3_seen"])
    items, off["fwd3_line"] = src.fwd3_items(paths["fwd3"], after_line=int(off["fwd3_line"]), data=pipe.data, cfg=pipe.cfg, seen=seen3)
    st["fwd3_seen"] = sorted(seen3)
    for it in items:
        counts[pipe.handle(it, now)["code"]] += 1

    counts["followups"] += pipe.update_trackers(now)
    counts["summaries"] += len(pipe.maybe_daily_summary(now))
    return dict(counts)


def prime(pipe: AlertPipeline, paths: dict[str, Path]) -> None:
    """First start: begin at the end of every record; nothing already written becomes an alert."""
    st = pipe.state
    p = paths["journal"]
    if p.exists():
        st["offsets"]["journal_offset"] = p.stat().st_size
        with p.open("rb") as f:
            st["offsets"]["journal_lines"] = sum(1 for _ in f)
    seen1: set[str] = set()
    _, st["offsets"]["fwd1_seq"] = src.fwd1_items(paths["fwd1"], after_seq=-1, data=pipe.data, cfg=pipe.cfg, seen=seen1, enrich=False)
    st["fwd1_seen"] = sorted(seen1)
    seen3: set[str] = set()
    _, st["offsets"]["fwd3_line"] = src.fwd3_items(paths["fwd3"], after_line=0, data=pipe.data, cfg=pipe.cfg, seen=seen3, enrich=False)
    st["fwd3_seen"] = sorted(seen3)


def cmd_watch(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    live_root = Path(args.live_root)
    out_dir = Path(args.out_dir)
    paths = _paths(cfg, live_root)
    env = _env(cfg, live_root, Path(args.env_file) if args.env_file else None)
    if not env.get("DATABASE_URL"):
        _say("DATABASE_URL not found in the env file")
        return 2
    data = PostgresReadOnly(env["DATABASE_URL"])
    sender = _sender(cfg, env)
    state_path = out_dir / "state.json"
    state = load_state(state_path)
    pipe = AlertPipeline(cfg, out_dir=out_dir, data=data, mode="watch", sender=sender, state=state,
                         fwd_ledgers=ForwardLedgers(paths["fwd1"], paths["fwd3"]))
    started = datetime.now(timezone.utc)
    fresh = not state_path.exists()
    if fresh:
        prime(pipe, paths)
        pipe.maybe_daily_summary(started)
        save_state(pipe.state, state_path)
    resumed = "fresh start" if fresh else "resumed from state.json"
    pipe.preview.note(f"## WATCH started {started.isoformat()} ({pipe.run_mode}, {resumed})")
    _say(f"trade alerts watch: mode={pipe.run_mode} out={out_dir}")
    stop_at = started + timedelta(hours=float(args.hours)) if args.hours else None
    if args.until:
        # A restarted dry run keeps its original end (owner ruling D51b restart).
        until = utc(args.until)
        stop_at = min(stop_at, until) if stop_at else until
    if stop_at:
        _say(f"watch stops at {stop_at.isoformat()}")
    try:
        while True:
            now = datetime.now(timezone.utc)
            try:
                counts = poll_once(pipe, paths, now)
                if any(v for v in counts.values()):
                    _say(f"poll {counts}")
            except Exception as exc:  # noqa: BLE001 - keep following; never raises into anything
                _say(f"poll failed: {type(exc).__name__}: {str(exc)[:200]}")
            save_state(pipe.state, state_path)
            if args.once or (stop_at and now >= stop_at):
                break
            time.sleep(float(cfg.raw["poll_seconds"]))
    finally:
        data.close()
    pipe.preview.note(f"## WATCH stopped {datetime.now(timezone.utc).isoformat()}")
    return 0


# --- backfill ------------------------------------------------------------------------------------------
def backfill(cfg: AlertConfig, data: MarketData, paths: dict[str, Path], out_dir: Path, *, now: datetime, hours: float,
             paper_trades: bool = True) -> dict[str, Any]:
    start = now - timedelta(hours=hours)
    pipe = AlertPipeline(cfg, out_dir=out_dir, data=data, mode="backfill",
                         fwd_ledgers=ForwardLedgers(paths["fwd1"], paths["fwd3"]))
    pipe.preview.note(
        f"## BACKFILL {start.isoformat()} -> {now.isoformat()} (dry run over real records; LATE judged at the time "
        "live recorded each decision)"
    )
    items: list[Any] = []
    j = paths["journal"]
    if j.exists():
        raw_lines = j.read_text(encoding="utf-8").splitlines()
        lines = []
        for i, ln in enumerate(raw_lines):
            try:
                if utc(json.loads(ln)["ts"]) >= start:
                    lines.append((i, ln))
            except (ValueError, KeyError):
                continue
        items += list(src.journal_items(lines, first_line_no=1, data=data, cfg=cfg))
    sigs1, _ = src.fwd1_items(paths["fwd1"], after_seq=-1, data=data, cfg=cfg, seen=set())
    items += [s for s in sigs1 if s.decided_at >= start]
    its3, _ = src.fwd3_items(paths["fwd3"], after_line=0, data=data, cfg=cfg, seen=set())
    items += [s for s in its3 if (s.signal if isinstance(s, src.SkipRecord) else s).decided_at >= start]
    if paper_trades:
        first = src.first_journal_ts(j)
        end = min(first, now) if first else now
        if end > start:
            items += src.paper_trade_items(data.paper_trades_opened(start, end), data=data, cfg=cfg)

    def when(it: Any) -> datetime:
        return (it.signal if isinstance(it, src.SkipRecord) else it).decided_at

    codes: collections.Counter[str] = collections.Counter()
    for it in sorted(items, key=when):
        row = pipe.handle(it, when(it))
        codes[row["code"]] += 1
    followups = pipe.update_trackers(now)
    groups = merge_stats(pipe.state["stats"])
    pipe.emit_summary(f"{start.isoformat()} -> {now.isoformat()}", groups, now, title="BACKFILL SUMMARY",
                      alerts=all_summary_alerts(pipe.state))
    return {"codes": dict(codes), "followups": followups, "groups": groups, "start": start.isoformat(), "now": now.isoformat()}


def cmd_backfill(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    live_root = Path(args.live_root)
    env = _env(cfg, live_root, Path(args.env_file) if args.env_file else None)
    if not env.get("DATABASE_URL"):
        _say("DATABASE_URL not found in the env file")
        return 2
    data = PostgresReadOnly(env["DATABASE_URL"])
    try:
        hours = float(args.hours) if args.hours else float(cfg.raw["backfill_hours"])
        out = backfill(cfg, data, _paths(cfg, live_root), Path(args.out_dir), now=datetime.now(timezone.utc),
                       hours=hours, paper_trades=not args.journal_only)
    finally:
        data.close()
    _say(f"backfill {out['start']} -> {out['now']}")
    _say(f"codes {out['codes']} followups {out['followups']}")
    _say(f"groups {out['groups']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.alerts.runner")
    ap.add_argument("command", choices=["watch", "backfill"])
    ap.add_argument("--live-root", default=str(REPO_ROOT), help="checkout whose data/ and .env are read (read-only)")
    ap.add_argument("--env-file", default=None, help="defaults to <live-root>/.env")
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "data" / "alerts"))
    ap.add_argument("--config", default=None, help="defaults to app/alerts/trade_alerts_config.json")
    ap.add_argument("--hours", type=float, default=None, help="watch: stop after N hours; backfill: window")
    ap.add_argument("--once", action="store_true", help="watch: one poll and exit")
    ap.add_argument("--until", default=None, help="watch: stop at this UTC time (ISO 8601)")
    ap.add_argument("--journal-only", action="store_true", help="backfill: skip pre-journal paper positions")
    args = ap.parse_args(argv)
    return cmd_watch(args) if args.command == "watch" else cmd_backfill(args)


if __name__ == "__main__":
    sys.exit(main())
