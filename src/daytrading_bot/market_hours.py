"""Handelszeiten, Feiertage und Zeitzonen (Xetra)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from .config import MarketConfig


def easter_sunday(year: int) -> date:
    """Ostersonntag nach der Gausschen Osterformel (gregorianischer Kalender)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


@lru_cache(maxsize=None)
def xetra_holidays(year: int) -> frozenset[date]:
    """Regelmaessige handelsfreie Tage an Xetra.

    Neujahr, Karfreitag, Ostermontag, 1. Mai, Heiligabend, beide Weihnachtstage
    und Silvester. Abweichungen (z. B. Sondertage) ueber ``market.extra_holidays``.
    """
    easter = easter_sunday(year)
    return frozenset(
        {
            date(year, 1, 1),
            easter - timedelta(days=2),
            easter + timedelta(days=1),
            date(year, 5, 1),
            date(year, 12, 24),
            date(year, 12, 25),
            date(year, 12, 26),
            date(year, 12, 31),
        }
    )


class MarketCalendar:
    """Beantwortet: Ist Handel? Duerfen jetzt Kaeufe vorgeschlagen werden?"""

    def __init__(self, cfg: MarketConfig) -> None:
        self.cfg = cfg
        self.tz = ZoneInfo(cfg.timezone)
        self._extra = frozenset(cfg.extra_holidays)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def local(self, moment: datetime) -> datetime:
        if moment.tzinfo is None:
            return moment.replace(tzinfo=self.tz)
        return moment.astimezone(self.tz)

    def is_holiday(self, day: date) -> bool:
        return day in xetra_holidays(day.year) or day in self._extra

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and not self.is_holiday(day)

    def session(self, day: date) -> tuple[datetime, datetime]:
        """Handelsbeginn und -ende eines Tages als Zeitpunkte."""
        start = datetime.combine(day, self.cfg.open, tzinfo=self.tz)
        end = datetime.combine(day, self.cfg.close, tzinfo=self.tz)
        return start, end

    def is_open(self, moment: datetime) -> bool:
        moment = self.local(moment)
        if not self.is_trading_day(moment.date()):
            return False
        start, end = self.session(moment.date())
        return start <= moment < end

    def should_scan(self, moment: datetime, grace_minutes: int) -> bool:
        """Lohnt sich eine Abfrage? Waehrend der Handelszeit plus Nachlauf."""
        moment = self.local(moment)
        if not self.is_trading_day(moment.date()):
            return False
        start, end = self.session(moment.date())
        return start < moment <= end + timedelta(minutes=grace_minutes)

    def next_open(self, moment: datetime) -> datetime:
        """Naechster Handelsbeginn ab ``moment`` (heute, falls noch nicht eroeffnet)."""
        moment = self.local(moment)
        day = moment.date()
        for _ in range(30):
            if self.is_trading_day(day):
                start, _ = self.session(day)
                if start > moment:
                    return start
            day += timedelta(days=1)
        raise RuntimeError("Kein Handelstag in den naechsten 30 Tagen gefunden")

    def in_entry_window(self, bar_end: datetime) -> bool:
        """Darf zu diesem Kerzenende ein Kauf vorgeschlagen werden?"""
        t = self.local(bar_end).time()
        return self.cfg.first_entry <= t <= self.cfg.last_entry

    def must_be_flat(self, bar_end: datetime) -> bool:
        """Ist es Zeit, Daytrading-Positionen zu schliessen?"""
        if self.cfg.flat_at is None:
            return False
        return self.local(bar_end).time() >= self.cfg.flat_at
