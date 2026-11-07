from datetime import date

import pytest

from conftest import berlin
from daytrading_bot.config import MarketConfig
from daytrading_bot.market_hours import MarketCalendar, easter_sunday, xetra_holidays


@pytest.mark.parametrize(
    "year, expected",
    [(2024, date(2024, 3, 31)), (2025, date(2025, 4, 20)), (2026, date(2026, 4, 5)), (2027, date(2027, 3, 28))],
)
def test_easter(year, expected):
    assert easter_sunday(year) == expected


def test_holidays_2026():
    hol = xetra_holidays(2026)
    for d in [date(2026, 1, 1), date(2026, 4, 3), date(2026, 4, 6), date(2026, 5, 1), date(2026, 12, 24), date(2026, 12, 31)]:
        assert d in hol
    assert date(2026, 10, 3) not in hol  # Tag der Deutschen Einheit: Xetra handelt


def test_open_and_closed():
    cal = MarketCalendar(MarketConfig())
    assert cal.is_open(berlin(2026, 11, 6, 10, 0))  # Freitag
    assert not cal.is_open(berlin(2026, 11, 6, 8, 59))
    assert not cal.is_open(berlin(2026, 11, 6, 17, 30))
    assert not cal.is_open(berlin(2026, 11, 7, 10, 0))  # Samstag
    assert not cal.is_open(berlin(2026, 12, 24, 10, 0))  # Heiligabend


def test_next_open_skips_weekend_and_holidays():
    cal = MarketCalendar(MarketConfig())
    assert cal.next_open(berlin(2026, 11, 7, 12, 0)) == berlin(2026, 11, 9, 9, 0)
    assert cal.next_open(berlin(2026, 12, 23, 18, 0)) == berlin(2026, 12, 28, 9, 0)
    assert cal.next_open(berlin(2026, 11, 9, 8, 0)) == berlin(2026, 11, 9, 9, 0)


def test_extra_holiday():
    cal = MarketCalendar(MarketConfig(extra_holidays=(date(2026, 11, 9),)))
    assert not cal.is_trading_day(date(2026, 11, 9))


def test_entry_window_and_flat():
    cal = MarketCalendar(MarketConfig())
    assert not cal.in_entry_window(berlin(2026, 11, 6, 9, 10))
    assert cal.in_entry_window(berlin(2026, 11, 6, 9, 15))
    assert cal.in_entry_window(berlin(2026, 11, 6, 17, 0))
    assert not cal.in_entry_window(berlin(2026, 11, 6, 17, 5))
    assert not cal.must_be_flat(berlin(2026, 11, 6, 17, 15))
    assert cal.must_be_flat(berlin(2026, 11, 6, 17, 20))
    assert not MarketCalendar(MarketConfig(flat_at=None)).must_be_flat(berlin(2026, 11, 6, 17, 25))


def test_should_scan_includes_grace_period():
    cal = MarketCalendar(MarketConfig())
    assert not cal.should_scan(berlin(2026, 11, 6, 9, 0), 20)
    assert cal.should_scan(berlin(2026, 11, 6, 9, 0, 20), 20)
    assert cal.should_scan(berlin(2026, 11, 6, 17, 45), 20)
    assert not cal.should_scan(berlin(2026, 11, 6, 17, 55), 20)
