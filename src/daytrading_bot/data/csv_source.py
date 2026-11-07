"""Kerzen aus CSV-Dateien lesen und schreiben (Backtests, eigener Datensatz)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .base import COLUMNS, DataError, normalize_bars


def history_path(directory: str | Path, symbol: str, interval_minutes: int) -> Path:
    return Path(directory) / f"{symbol}_{interval_minutes}m.csv"


def load_csv(path: str | Path, tz: str) -> pd.DataFrame:
    """CSV mit Spalten timestamp,open,high,low,close,volume einlesen."""
    path = Path(path)
    if not path.exists():
        raise DataError(f"Datei {path} nicht gefunden")
    df = pd.read_csv(path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    time_col = next((c for c in ("timestamp", "datetime", "date", "time") if c in df.columns), None)
    if time_col is None:
        raise DataError(f"{path}: Spalte 'timestamp' fehlt")
    raw = df.pop(time_col).astype(str).str.strip()
    has_offset = raw.str.contains(r"(?:[+-]\d{2}:?\d{2}|Z)$", regex=True)
    try:
        if has_offset.all():
            stamps = pd.to_datetime(raw, utc=True, format="ISO8601")
        elif not has_offset.any():
            # Ohne Zeitzonenangabe: Uhrzeit gilt in der Boersen-Zeitzone.
            stamps = pd.to_datetime(raw, format="ISO8601").dt.tz_localize(
                tz, ambiguous="NaT", nonexistent="NaT"
            )
        else:
            raise DataError(f"{path}: Zeitangaben teils mit, teils ohne Zeitzone")
    except (ValueError, TypeError) as exc:
        raise DataError(f"{path}: Zeitangaben nicht lesbar ({exc})") from exc
    df.index = pd.DatetimeIndex(stamps)
    df = df[df.index.notna()]
    return normalize_bars(df, tz)


def save_csv(df: pd.DataFrame, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df[COLUMNS].copy()
    out.index.name = "timestamp"
    tmp = path.with_suffix(path.suffix + ".tmp")
    out.to_csv(tmp, date_format="%Y-%m-%dT%H:%M:%S%z")
    tmp.replace(path)


def merge_into_csv(df: pd.DataFrame, path: str | Path, tz: str) -> int:
    """Neue Kerzen an eine bestehende CSV anhaengen (ohne Dubletten).

    Gibt die Anzahl der Kerzen in der Datei zurueck.
    """
    path = Path(path)
    if df.empty:
        return 0
    if path.exists():
        old = load_csv(path, tz)
        merged = pd.concat([old, df])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    else:
        merged = df
    save_csv(merged, path)
    return len(merged)


class CsvSource:
    """Liest ``<SYMBOL>_<N>m.csv`` aus einem Ordner (z. B. data/history)."""

    def __init__(self, directory: str | Path, tz: str, interval_minutes: int) -> None:
        self.directory = Path(directory)
        self.tz = tz
        self.interval_minutes = interval_minutes

    def fetch(self, symbol: str, days: int) -> pd.DataFrame:
        df = load_csv(history_path(self.directory, symbol, self.interval_minutes), self.tz)
        if df.empty:
            return df
        start = df.index[-1] - pd.Timedelta(days=days)
        return df[df.index > start]
