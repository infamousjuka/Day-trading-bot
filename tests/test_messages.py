import re
from dataclasses import replace

from conftest import StubStrategy, berlin, make_bars
from daytrading_bot.config import Instrument
from daytrading_bot.engine import SignalEngine
from daytrading_bot.market_hours import MarketCalendar
from daytrading_bot.messages import (
    fmt_eur,
    fmt_num,
    fmt_pct,
    fmt_price,
    fmt_signed_eur,
    format_positions,
    format_signal,
    format_trades_summary,
    html_to_text,
)
from daytrading_bot.portfolio import Portfolio
from daytrading_bot.risk import CostModel

ALLOWED_TAGS = {"b", "i", "code"}


def _tags(text):
    return set(re.findall(r"</?([a-z\-]+)", text))


def test_number_formats():
    assert fmt_num(1234567.891) == "1.234.567,89"
    assert fmt_eur(1824) == "1.824,00 €"
    assert fmt_price(7.5) == "7,500 €"
    assert fmt_signed_eur(-12.3) == "−12,30 €"
    assert fmt_signed_eur(0.001) == "±0,00 €"
    assert fmt_pct(2.857) == "+2,86 %"


def _signals(cfg):
    strat = StubStrategy(entries={0})
    pf = Portfolio(capital=cfg.risk.capital)
    engine = SignalEngine(
        strat,
        MarketCalendar(cfg.market),
        pf,
        replace(cfg.risk, min_reward_cost_ratio=0),
        CostModel.from_config(cfg.costs),
        5,
    )
    df = strat.prepare(make_bars([182.4, 183.0, 184.5], start=berlin(2026, 11, 6, 10, 30)))
    inst = Instrument("SAP.DE", "SAP <SE>")
    out = []
    for i in range(len(df)):
        out.extend(engine.on_bar(inst, df, i))
    return out, pf


def test_buy_and_sell_messages(cfg):
    signals, _ = _signals(cfg)
    buy, sell = signals
    text = format_signal(buy, 20)
    assert "KAUFEN: SAP &lt;SE&gt;" in text  # Name wird HTML-sicher
    assert "182,40 €" in text and "Kerze 10:30–10:35" in text
    assert "Stop-Loss" in text and "Kursziel" in text and "Limit-Kauf" in text
    assert _tags(text) <= ALLOWED_TAGS
    sell_text = format_signal(sell, 20)
    assert "VERKAUFEN" in sell_text and "Kursziel erreicht" in sell_text
    assert "nach Kosten" in sell_text
    assert _tags(sell_text) <= ALLOWED_TAGS
    buy.delay_minutes = 30
    assert "30 Min. alt" in format_signal(buy, 20)
    assert "<b>" not in html_to_text(text)


def test_positions_and_summary(cfg):
    pf = Portfolio(capital=10_000)
    assert "Keine offenen" in format_positions(pf, CostModel(1, 0.05, 0.02))
    pf.open_position("SAP.DE", "SAP", 10, 100.0, berlin(2026, 11, 6, 10, 0), 99.0, 102.0)
    pf.last_price["SAP.DE"] = (101.0, berlin(2026, 11, 6, 10, 30).isoformat())
    text = format_positions(pf, CostModel(1, 0.05, 0.02))
    assert "SAP" in text and "Zuletzt 101,00 €" in text
    signals, pf2 = _signals(cfg)
    summary = format_trades_summary(pf2.trades, berlin(2026, 11, 6, 0, 0).date(), pf2.realized_on(berlin(2026, 11, 6, 0, 0).date()))
    assert "Tagesbilanz Fr 06.11.2026" in summary and "Trades: 1" in summary
    assert _tags(summary) <= ALLOWED_TAGS
