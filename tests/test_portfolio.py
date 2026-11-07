from datetime import date

import pytest

from conftest import berlin
from daytrading_bot.portfolio import Portfolio, StateStore, append_journal
from daytrading_bot.risk import CostModel

COSTS = CostModel(1.0, 0.05, 0.02)


def test_open_close_and_realized():
    pf = Portfolio(capital=10_000)
    pf.open_position("SAP.DE", "SAP", 10, 100.0, berlin(2026, 11, 6, 10, 0), 99.0, 102.0)
    assert pf.invested() == 1000.0
    with pytest.raises(ValueError):
        pf.open_position("SAP.DE", "SAP", 1, 100.0, berlin(2026, 11, 6, 10, 0), 99.0, 102.0)
    trade = pf.close_position("SAP.DE", 102.0, berlin(2026, 11, 6, 11, 0), "Ziel", "target", COSTS)
    assert trade.pnl_net == pytest.approx(COSTS.net_pnl(100.0, 102.0, 10))
    assert pf.realized_on(date(2026, 11, 6)) == pytest.approx(trade.pnl_net)
    assert pf.trades_on(date(2026, 11, 6)) == [trade]
    pf.reprice_trade(trade, 101.0, COSTS)
    assert pf.realized_on(date(2026, 11, 6)) == pytest.approx(COSTS.net_pnl(100.0, 101.0, 10))


def test_find_by_symbol_name_or_short_symbol():
    pf = Portfolio(capital=10_000)
    pos = pf.open_position("SAP.DE", "SAP", 10, 100.0, berlin(2026, 11, 6, 10, 0), 99.0, 102.0)
    assert pf.find("sap") is pos
    assert pf.find("SAP.DE") is pos
    assert pf.find(pos.id) is pos
    assert pf.find("SIE") is None


def test_state_roundtrip(tmp_path):
    pf = Portfolio(capital=10_000, paused=True)
    pf.open_position("SAP.DE", "SAP", 10, 100.0, berlin(2026, 11, 6, 10, 0), 99.0, 102.0)
    pf.open_position("SIE.DE", "Siemens", 3, 200.0, berlin(2026, 11, 6, 10, 5), 198.0, 204.0)
    pf.close_position("SIE.DE", 199.0, berlin(2026, 11, 6, 11, 0), "Stop", "stop", COSTS)
    pf.last_bar["SAP.DE"] = berlin(2026, 11, 6, 10, 55).isoformat()
    pf.last_price["SAP.DE"] = (101.0, berlin(2026, 11, 6, 11, 0).isoformat())
    store = StateStore(tmp_path / "s" / "state.json")
    store.save(pf)
    loaded = store.load(capital=10_000)
    assert loaded.paused is True
    assert loaded.positions["SAP.DE"].entry_time == berlin(2026, 11, 6, 10, 0)
    assert loaded.realized == pf.realized
    assert loaded.trades[0].reason_code == "stop"
    assert loaded.last_price["SAP.DE"][0] == 101.0
    assert StateStore(tmp_path / "missing.json").load(500).capital == 500


def test_journal_appends(tmp_path):
    pf = Portfolio(capital=10_000)
    pf.open_position("SAP.DE", "SAP", 10, 100.0, berlin(2026, 11, 6, 10, 0), 99.0, 102.0)
    trade = pf.close_position("SAP.DE", 102.0, berlin(2026, 11, 6, 11, 0), "Ziel", "target", COSTS)
    path = tmp_path / "journal.csv"
    append_journal(path, [trade])
    append_journal(path, [trade])
    lines = path.read_text().strip().splitlines()
    assert lines[0].startswith("id,symbol")
    assert len(lines) == 3
