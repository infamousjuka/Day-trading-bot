from dataclasses import replace
from datetime import date

import pytest

from conftest import StubStrategy, berlin, make_bars
from daytrading_bot.config import Instrument, RiskConfig
from daytrading_bot.engine import BUY, SELL, SignalEngine
from daytrading_bot.market_hours import MarketCalendar
from daytrading_bot.portfolio import Portfolio
from daytrading_bot.risk import CostModel

INST = Instrument("TEST.DE", "Test AG")


def make_engine(cfg, strategy, portfolio=None, risk=None):
    portfolio = portfolio or Portfolio(capital=cfg.risk.capital)
    engine = SignalEngine(
        strategy,
        MarketCalendar(cfg.market),
        portfolio,
        risk or replace(cfg.risk, min_reward_cost_ratio=0),
        CostModel.from_config(cfg.costs),
        cfg.interval_minutes,
    )
    return engine, portfolio


def run(engine, strategy, df, start=0):
    prepared = strategy.prepare(df)
    out = []
    for i in range(start, len(prepared)):
        out.extend(engine.on_bar(INST, prepared, i))
    return out


def test_buy_then_stop_loss(cfg):
    strat = StubStrategy(entries={0})
    engine, pf = make_engine(cfg, strat)
    # 10:00-Kerze -> Kauf bei 100, Stop = 100 - 2*0.5 = 99
    df = make_bars([100.0, 100.2, 98.8, 99.5], start=berlin(2026, 11, 6, 10, 0), spread=0.05)
    signals = run(engine, strat, df)
    assert [s.action for s in signals] == [BUY, SELL]
    buy, sell = signals
    assert buy.price == 100.0 and buy.stop == pytest.approx(99.0) and buy.take_profit == pytest.approx(102.0)
    assert buy.qty >= 1 and buy.plan is not None
    assert buy.bar_end == berlin(2026, 11, 6, 10, 5)
    assert sell.reason_code == "stop"
    assert sell.trade.pnl_net < 0
    assert not pf.positions


def test_take_profit(cfg):
    strat = StubStrategy(entries={0})
    engine, _ = make_engine(cfg, strat)
    df = make_bars([100.0, 101.0, 102.5], start=berlin(2026, 11, 6, 10, 0))
    signals = run(engine, strat, df)
    assert signals[-1].action == SELL and signals[-1].reason_code == "target"
    assert signals[-1].trade.pnl_net > 0


def test_stop_wins_if_both_levels_in_one_bar(cfg):
    strat = StubStrategy(entries={0})
    engine, _ = make_engine(cfg, strat)
    df = make_bars([100.0, 100.0], start=berlin(2026, 11, 6, 10, 0), spread=3.0)
    assert run(engine, strat, df)[-1].reason_code == "stop"


def test_strategy_exit(cfg):
    strat = StubStrategy(entries={0}, exits={2})
    engine, _ = make_engine(cfg, strat)
    df = make_bars([100.0, 100.1, 100.2], start=berlin(2026, 11, 6, 10, 0))
    assert run(engine, strat, df)[-1].reason_code == "signal"


def test_end_of_day_exit(cfg):
    strat = StubStrategy(entries={0})
    engine, _ = make_engine(cfg, strat)
    # Einstieg 16:55-17:00, danach seitwaerts bis 17:20
    df = make_bars([100.0] * 6, start=berlin(2026, 11, 6, 16, 55))
    signals = run(engine, strat, df)
    assert [s.action for s in signals] == [BUY, SELL]
    assert signals[1].reason_code == "eod"
    assert signals[1].bar_end == berlin(2026, 11, 6, 17, 20)


def test_overnight_position_is_closed_next_morning(cfg):
    strat = StubStrategy()
    engine, pf = make_engine(cfg, strat)
    pf.open_position("TEST.DE", "Test AG", 5, 100.0, berlin(2026, 11, 5, 16, 0), 90.0, 110.0)
    df = make_bars([100.0], start=berlin(2026, 11, 6, 9, 0))
    signals = run(engine, strat, df)
    assert signals[0].reason_code == "overnight"


def test_no_entry_outside_window(cfg):
    strat = StubStrategy(entries={0, 1})
    engine, _ = make_engine(cfg, strat)
    early = make_bars([100.0], start=berlin(2026, 11, 6, 9, 5))  # endet 09:10
    late = make_bars([100.0], start=berlin(2026, 11, 6, 17, 0))  # endet 17:05
    assert run(engine, strat, early) == []
    assert run(engine, strat, late) == []


def test_daily_loss_limit_blocks_entries(cfg):
    strat = StubStrategy(entries={0})
    engine, pf = make_engine(cfg, strat)
    pf.realized[date(2026, 11, 6).isoformat()] = -cfg.risk.max_daily_loss
    df = make_bars([100.0], start=berlin(2026, 11, 6, 10, 0))
    assert run(engine, strat, df) == []
    assert engine.daily_limit_hit(date(2026, 11, 6))


def test_max_open_positions(cfg):
    strat = StubStrategy(entries={0})
    risk = replace(cfg.risk, max_open_positions=1, min_reward_cost_ratio=0)
    engine, pf = make_engine(cfg, strat, risk=risk)
    pf.open_position("OTHER.DE", "Other", 1, 50.0, berlin(2026, 11, 6, 10, 0), 49.0, 52.0)
    df = make_bars([100.0], start=berlin(2026, 11, 6, 10, 0))
    assert run(engine, strat, df) == []


def test_rejected_plan_is_recorded(cfg):
    strat = StubStrategy(entries={0}, atr=0.001)
    engine, pf = make_engine(cfg, strat, risk=RiskConfig(min_reward_cost_ratio=3.0))
    df = make_bars([100.0], start=berlin(2026, 11, 6, 10, 0))
    assert run(engine, strat, df) == []
    assert "Kosten" in engine.last_rejection["TEST.DE"]
    assert sum(engine.rejections.values()) == 1
    assert not pf.positions


def test_allow_entries_false(cfg):
    strat = StubStrategy(entries={0})
    engine, _ = make_engine(cfg, strat)
    prepared = strat.prepare(make_bars([100.0], start=berlin(2026, 11, 6, 10, 0)))
    assert engine.on_bar(INST, prepared, 0, allow_entries=False) == []
