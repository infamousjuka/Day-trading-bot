"""Live-Kurse ueber yfinance (Yahoo Finance).

Hinweis: yfinance ist eine inoffizielle Schnittstelle. Yahoo kann sie jederzeit
aendern oder drosseln, Kurse deutscher Boersen koennen verzoegert sein.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from .base import DataError, normalize_bars

log = logging.getLogger(__name__)

# Yahoo liefert Intraday-Kerzen nur fuer begrenzte Zeitraeume.
MAX_DAYS = {1: 7, 2: 59, 5: 59, 15: 59, 30: 59, 60: 729}


class YahooSource:
    def __init__(self, interval_minutes: int, tz: str) -> None:
        self.interval_minutes = interval_minutes
        self.tz = tz

    def fetch(self, symbol: str, days: int) -> pd.DataFrame:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - Abhaengigkeit fehlt
            raise DataError("yfinance ist nicht installiert (pip install yfinance)") from exc

        max_days = MAX_DAYS.get(self.interval_minutes, 59)
        if days > max_days:
            log.info("Yahoo liefert %s-Min-Kerzen nur fuer %s Tage", self.interval_minutes, max_days)
            days = max_days
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        try:
            raw = yf.Ticker(symbol).history(
                start=start,
                end=end,
                interval=f"{self.interval_minutes}m",
                prepost=False,
                auto_adjust=False,
                actions=False,
                raise_errors=True,
            )
        except Exception as exc:  # yfinance wirft unterschiedliche Fehlertypen
            raise DataError(
                f"Yahoo-Abfrage für {symbol} fehlgeschlagen: {exc} "
                "(Internetverbindung und Symbol prüfen; Yahoo meldet bei Netzproblemen oft 'delisted')"
            ) from exc
        if raw is None or raw.empty:
            raise DataError(f"Keine Kurse von Yahoo für {symbol}")
        return normalize_bars(raw, self.tz)
