import numpy as np
import pandas as pd

from conftest import berlin, make_bars
from daytrading_bot.indicators import atr, ema, intraday_vwap, rsi


def test_ema_matches_manual_calculation():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = ema(s, 3)
    alpha = 2 / (3 + 1)
    expected = [1.0]
    for v in s.iloc[1:]:
        expected.append(alpha * v + (1 - alpha) * expected[-1])
    assert out.iloc[:2].isna().all()  # Aufwaermphase
    np.testing.assert_allclose(out.iloc[2:], expected[2:])


def test_rsi_extremes():
    up = pd.Series(np.arange(1.0, 40.0))
    down = pd.Series(np.arange(40.0, 1.0, -1))
    flat = pd.Series([5.0] * 40)
    assert rsi(up, 14).iloc[-1] == 100.0
    assert rsi(down, 14).iloc[-1] == 0.0
    assert rsi(flat, 14).iloc[-1] == 50.0
    assert rsi(up, 14).iloc[:14].isna().all()


def test_rsi_range():
    rng = np.random.default_rng(1)
    s = pd.Series(100 + rng.normal(0, 1, 500).cumsum())
    values = rsi(s, 14).dropna()
    assert values.between(0, 100).all()


def test_atr_constant_range():
    df = make_bars([100.0] * 40, spread=0.5)
    assert abs(atr(df, 14).iloc[-1] - 1.0) < 1e-9


def test_vwap_restarts_each_day():
    day1 = make_bars([10.0, 12.0], start=berlin(2026, 11, 2, 9, 0), spread=0.0)
    day2 = make_bars([20.0, 22.0], start=berlin(2026, 11, 3, 9, 0), spread=0.0)
    df = pd.concat([day1, day2])
    v = intraday_vwap(df)
    assert v.iloc[0] == 10.0
    assert v.iloc[2] == 20.0  # neuer Tag, kein Einfluss von gestern


def test_vwap_without_volume_is_nan():
    df = make_bars([10.0, 11.0], volume=0.0)
    assert intraday_vwap(df).isna().all()
