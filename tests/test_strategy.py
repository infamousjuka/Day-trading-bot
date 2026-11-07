import numpy as np
import pandas as pd

from conftest import make_bars
from daytrading_bot.config import StrategyConfig
from daytrading_bot.strategy import EmaCrossStrategy


def _down_then_up():
    rng = np.random.default_rng(3)
    down = 100 - np.arange(40) * 0.05 + rng.normal(0, 0.03, 40)
    up = down[-1] + np.arange(1, 41) * 0.04 + rng.normal(0, 0.05, 40)
    return list(down) + list(up)


def test_cross_up_detected_once():
    strat = EmaCrossStrategy(StrategyConfig(use_vwap_filter=False, rsi_min=0, rsi_max=100))
    df = strat.prepare(make_bars(_down_then_up()))
    crosses = np.flatnonzero(df["cross_up"].to_numpy())
    assert len(crosses) >= 1
    first = crosses[0]
    assert df["ema_fast"].iat[first] > df["ema_slow"].iat[first]
    assert df["ema_fast"].iat[first - 1] <= df["ema_slow"].iat[first - 1]
    reason = strat.entry_reason(df, first)
    assert reason is not None and "kreuzt EMA 21 nach oben" in reason


def test_rsi_filter_blocks_entry():
    strat = EmaCrossStrategy(StrategyConfig(use_vwap_filter=False, rsi_min=99, rsi_max=100))
    df = strat.prepare(make_bars(_down_then_up()))
    for i in np.flatnonzero(df["cross_up"].to_numpy()):
        assert strat.entry_reason(df, int(i)) is None


def test_vwap_filter_blocks_entry_below_vwap():
    strat = EmaCrossStrategy(StrategyConfig(rsi_min=0, rsi_max=100))
    df = strat.prepare(make_bars(_down_then_up()))
    i = int(np.flatnonzero(df["cross_up"].to_numpy())[0])
    df.loc[df.index[i], "vwap"] = df["close"].iat[i] + 1.0
    assert strat.entry_reason(df, i) is None
    df.loc[df.index[i], "vwap"] = df["close"].iat[i] - 1.0
    assert "VWAP" in strat.entry_reason(df, i)


def test_no_entry_during_warmup():
    strat = EmaCrossStrategy(StrategyConfig(use_vwap_filter=False, rsi_min=0, rsi_max=100))
    df = strat.prepare(make_bars(_down_then_up()))
    df["cross_up"] = True
    assert all(strat.entry_reason(df, i) is None for i in range(strat.warmup_bars))


def test_exit_on_cross_down():
    strat = EmaCrossStrategy(StrategyConfig())
    closes = [200.0 - c for c in _down_then_up()]  # erst steigend, dann fallend
    df = strat.prepare(make_bars(closes))
    downs = np.flatnonzero(df["cross_down"].to_numpy())
    assert len(downs) >= 1
    assert "nach unten" in strat.exit_reason(df, int(downs[0]))
    off = EmaCrossStrategy(StrategyConfig(exit_on_cross_down=False))
    assert off.exit_reason(df, int(downs[0])) is None
