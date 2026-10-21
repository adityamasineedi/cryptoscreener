"""Telegram trade alerts (owner message 2026-10-21). Output-only.

A sidecar reads the live paper decision journal, the paper ledger and OHLCV read-only and turns each new
signal into an alert in the owner's format. No paper, trading or decision path imports this package or reads
its state (``tests/test_trade_alerts_replay.py`` checks both). Delivery is off by default
(``trade_alerts_config.json``: ``enabled`` false, ``dry_run`` true); in dry run alerts are only rendered to
``data/alerts/preview.md`` and logged to ``data/alerts/alerts.jsonl``.
"""
