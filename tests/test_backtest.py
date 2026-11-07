import pandas as pd
import pytest

from conftest import StubStrategy, berlin, make_bars
from daytrading_bot import backtest as bt
from daytrading_bot.data import load_csv


def test_backtest_on_sample_data(cfg, sample_csv, tmp_path):
    data = {"SYNTH": load_csv(sample_csv, "Europe/Berlin")}
    result = bt.run_backtest(cfg, data)
    s = result.stats
    assert s["trades"] == len(result.trades) > 0
    assert s["wins"] + s["losses"] == s["trades"]
    assert s["costs"] > 0
    assert s["net_pnl"] == pytest.approx(s["gross_pnl"] - s["costs"])
    assert result.equity.iloc[-1] == pytest.approx(cfg.risk.capital + s["net_pnl"])
    assert s["max_drawdown"] <= 0
    for t in result.trades:
        assert t.qty >= 1
        assert t.exit_time > t.entry_time
        # Daytrading: kein Trade ueber Nacht
        assert t.exit_time.date() == t.entry_time.date()
    # Nie zwei Positionen gleichzeitig im selben Wert
    trades = sorted(result.trades, key=lambda t: t.entry_time)
    for a, b in zip(trades, trades[1:]):
        assert b.entry_time >= a.exit_time

    out = bt.write_report(result, tmp_path / "report")
    assert (out / "trades.csv").exists() and (out / "equity.csv").exists()
    text = bt.format_stats(result)
    assert "Trefferquote" in text and "Kosten gesamt" in text


def test_fills_at_next_bar_open(cfg, monkeypatch):
    monkeypatch.setattr(bt, "EmaCrossStrategy", lambda _cfg: StubStrategy(entries={0}, exits={2}))
    df = make_bars([100.0, 100.5, 101.0, 101.5], start=berlin(2026, 11, 6, 10, 0))
    result = bt.run_backtest(cfg, {"TEST.DE": df})
    trade = result.trades[0]
    assert trade.entry_price == df["open"].iat[1]  # Signal Kerze 0 -> Kauf zur Eroeffnung Kerze 1
    assert trade.exit_price == df["open"].iat[3]   # Ausstieg Kerze 2 -> Verkauf Eroeffnung Kerze 3
    assert result.stats["exit_signal"] == 1


def test_open_position_closed_at_data_end(cfg, monkeypatch):
    monkeypatch.setattr(bt, "EmaCrossStrategy", lambda _cfg: StubStrategy(entries={0}))
    df = make_bars([100.0, 100.2, 100.4], start=berlin(2026, 11, 6, 10, 0))
    result = bt.run_backtest(cfg, {"TEST.DE": df})
    assert result.trades[-1].reason_code == "end"


def test_empty_data_raises(cfg):
    with pytest.raises(ValueError):
        bt.run_backtest(cfg, {"X": pd.DataFrame(columns=["open", "high", "low", "close", "volume"])})
