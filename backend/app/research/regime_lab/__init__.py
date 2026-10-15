"""Regime Lab: regime-routed rules-based strategies on OHLCV.

A regime classifier (200 SMA, ADX, ATR percentile) decides which strategies may
trade each bar. Signals fire on bar close and fill at the next open, with fees
and per-tier slippage. Research only: no orders are placed.
"""
