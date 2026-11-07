"""Gemeinsame Funktionen fuer alle Datenquellen."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]


class DataError(RuntimeError):
    """Kurse konnten nicht geladen werden."""


class DataSource(Protocol):
    """Liefert Kerzen (Index = Kerzenbeginn, Spalten = COLUMNS)."""

    def fetch(self, symbol: str, days: int) -> pd.DataFrame: ...


def normalize_bars(df: pd.DataFrame, tz: str | ZoneInfo) -> pd.DataFrame:
    """Spaltennamen vereinheitlichen, Zeitzone setzen, sortieren, Luecken entfernen."""
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], tz=tz))
    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    missing = [c for c in ["open", "high", "low", "close"] if c not in out.columns]
    if missing:
        raise DataError(f"Spalten fehlen: {missing}")
    if "volume" not in out.columns:
        out["volume"] = 0.0
    out = out[COLUMNS]
    index = pd.DatetimeIndex(pd.to_datetime(out.index))
    index = index.tz_localize(tz) if index.tz is None else index.tz_convert(tz)
    out.index = index
    out.index.name = "timestamp"
    out = out.apply(pd.to_numeric, errors="coerce").astype(float)
    out = out.dropna(subset=["open", "high", "low", "close"])
    out["volume"] = out["volume"].fillna(0.0)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def closed_bars(df: pd.DataFrame, now: datetime, interval_minutes: int) -> pd.DataFrame:
    """Nur abgeschlossene Kerzen behalten, die exakt im Raster liegen.

    Die gerade laufende Kerze (und eine evtl. angehaengte "Live-Zeile" mit
    krummer Uhrzeit) wuerde sonst Signale auf halbfertigen Daten erzeugen.
    """
    if df.empty:
        return df
    idx = df.index
    on_grid = (idx.second == 0) & (idx.microsecond == 0) & (idx.minute % interval_minutes == 0)
    finished = idx + timedelta(minutes=interval_minutes) <= now
    return df[on_grid & finished]


def bar_spacing_minutes(df: pd.DataFrame) -> float | None:
    """Haeufigster Abstand zwischen zwei Kerzen eines Tages in Minuten."""
    if len(df) < 2:
        return None
    diffs = pd.Series(df.index[1:] - df.index[:-1])
    same_day = pd.Series(df.index[1:].date == df.index[:-1].date)
    diffs = diffs[same_day.values]
    if diffs.empty:
        return None
    return float(diffs.mode().iloc[0].total_seconds() / 60.0)


def resample_bars(df: pd.DataFrame, interval_minutes: int) -> pd.DataFrame:
    """Feinere Kerzen (z. B. 1 Minute) zu groesseren (z. B. 5 Minuten) zusammenfassen."""
    rule = f"{interval_minutes}min"
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["open", "high", "low", "close"])
