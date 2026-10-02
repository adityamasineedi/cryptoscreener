from __future__ import annotations

from app.engines.mtf.indicators import williams_r


def test_williams_r_formula():
    # HH=110, LL=90, C=100 → (110-100)/(110-90)*-100 = -50
    highs = [100.0] * 13 + [110.0]
    lows = [100.0] * 13 + [90.0]
    closes = [100.0] * 14
    wr = williams_r(highs, lows, closes, 14)
    assert wr is not None
    assert abs(wr - (-50.0)) < 1e-9


def test_williams_r_insufficient_bars():
    assert williams_r([1, 2], [1, 2], [1, 2], 14) is None
