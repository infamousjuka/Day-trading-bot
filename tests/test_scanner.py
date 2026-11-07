from dataclasses import replace

import pandas as pd

from conftest import FakeSource, StubStrategy, berlin, make_bars
from daytrading_bot.engine import BUY, SELL, SignalEngine
from daytrading_bot.market_hours import MarketCalendar
from daytrading_bot.portfolio import Portfolio
from daytrading_bot.risk import CostModel
from daytrading_bot.scanner import Scanner


def make_scanner(cfg, source, strategy):
    cfg = replace(cfg, risk=replace(cfg.risk, min_reward_cost_ratio=0))
    cal = MarketCalendar(cfg.market)
    pf = Portfolio(capital=cfg.risk.capital)
    engine = SignalEngine(strategy, cal, pf, cfg.risk, CostModel.from_config(cfg.costs), cfg.interval_minutes)
    return Scanner(cfg, source, strategy, engine, pf, cal), pf


def test_uses_only_closed_bars_and_never_repeats(cfg):
    # Kerzen 10:00 .. 10:20; um 10:22 ist die 10:20-Kerze noch nicht fertig
    df = make_bars([100.0, 100.1, 100.2, 100.3, 100.4], start=berlin(2026, 11, 6, 10, 0))
    strat = StubStrategy(entries={3})  # Index 3 = 10:15-Kerze
    scanner, pf = make_scanner(cfg, FakeSource({"TEST.DE": df}, fail={"OTHER.DE"}), strat)

    report = scanner.scan(now=berlin(2026, 11, 6, 10, 22))
    assert [s.action for s in report.signals] == [BUY]
    assert report.signals[0].bar_end == berlin(2026, 11, 6, 10, 20)
    assert report.signals[0].delay_minutes == 2.0
    assert "OTHER.DE" in report.errors
    assert report.snapshots["TEST.DE"]["close"] == 100.3

    again = scanner.scan(now=berlin(2026, 11, 6, 10, 24))
    assert again.signals == []
    assert "TEST.DE" in pf.positions


def test_catches_up_on_missed_bars_for_exits(cfg):
    df = make_bars([100.0, 100.0, 100.0, 100.0], start=berlin(2026, 11, 6, 10, 0))
    strat = StubStrategy(entries={0})
    source = FakeSource({"TEST.DE": df.iloc[:1]})
    scanner, pf = make_scanner(cfg, source, strat)
    assert [s.action for s in scanner.scan(now=berlin(2026, 11, 6, 10, 5, 20)).signals] == [BUY]

    # Zwei neue Kerzen auf einmal, die erste reisst den Stop
    crash = df.copy()
    crash.loc[crash.index[1], "low"] = 90.0
    source.frames["TEST.DE"] = crash.iloc[:3]
    report = scanner.scan(now=berlin(2026, 11, 6, 10, 15, 20))
    assert [s.action for s in report.signals] == [SELL]
    assert report.signals[0].reason_code == "stop"
    assert report.closed_trades and not pf.positions


def test_stale_data_blocks_buys(cfg):
    df = make_bars([100.0, 100.0], start=berlin(2026, 11, 6, 10, 0))
    strat = StubStrategy(entries={1})
    scanner, pf = make_scanner(cfg, FakeSource({"TEST.DE": df}), strat)
    report = scanner.scan(now=berlin(2026, 11, 6, 11, 0))
    assert report.signals == []
    assert report.stale["TEST.DE"] > cfg.data.max_delay_minutes


def test_pause_blocks_buys_but_not_sells(cfg):
    df = make_bars([100.0, 100.0, 95.0], start=berlin(2026, 11, 6, 10, 0))
    strat = StubStrategy(entries={0, 1})
    source = FakeSource({"TEST.DE": df.iloc[:1]})
    scanner, pf = make_scanner(cfg, source, strat)
    pf.paused = True
    assert scanner.scan(now=berlin(2026, 11, 6, 10, 5, 20)).signals == []
    pf.paused = False
    source.frames["TEST.DE"] = df.iloc[:2]
    assert [s.action for s in scanner.scan(now=berlin(2026, 11, 6, 10, 10, 20)).signals] == [BUY]
    pf.paused = True
    source.frames["TEST.DE"] = df
    assert [s.action for s in scanner.scan(now=berlin(2026, 11, 6, 10, 15, 20)).signals] == [SELL]


def test_daily_limit_notice_once(cfg):
    df = make_bars([100.0], start=berlin(2026, 11, 6, 10, 0))
    scanner, pf = make_scanner(cfg, FakeSource({"TEST.DE": df}), StubStrategy())
    pf.realized["2026-11-06"] = -1000.0
    pf.flags["stale:X:2026-11-05"] = "1"
    first = scanner.scan(now=berlin(2026, 11, 6, 10, 5, 20))
    second = scanner.scan(now=berlin(2026, 11, 6, 10, 10, 20))
    assert len(first.notices) == 1 and "Tagesverlust" in first.notices[0]
    assert second.notices == []
    assert "stale:X:2026-11-05" not in pf.flags  # alte Merker werden aufgeraeumt


def test_records_bars_for_yahoo(cfg, tmp_path):
    df = make_bars([100.0, 101.0], start=berlin(2026, 11, 6, 10, 0))
    scanner, _ = make_scanner(cfg, FakeSource({"TEST.DE": df}), StubStrategy())
    scanner.record({"TEST.DE": df}, berlin(2026, 11, 6, 10, 7))
    saved = pd.read_csv(tmp_path / "data" / "history" / "TEST.DE_5m.csv")
    assert len(saved) == 1  # nur die abgeschlossene 10:00-Kerze
