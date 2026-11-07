"""Datenquellen fuer 5-Minuten-Kerzen (OHLCV)."""

from __future__ import annotations

from ..config import Config
from .base import (
    DataError,
    DataSource,
    bar_spacing_minutes,
    closed_bars,
    normalize_bars,
    resample_bars,
)
from .csv_source import CsvSource, history_path, load_csv, merge_into_csv
from .yahoo import YahooSource


def create_source(cfg: Config) -> DataSource:
    """Datenquelle laut config.yaml erzeugen."""
    if cfg.data.source == "csv":
        return CsvSource(cfg.path(cfg.data.csv_dir), cfg.market.timezone, cfg.interval_minutes)
    return YahooSource(cfg.interval_minutes, cfg.market.timezone)


__all__ = [
    "CsvSource",
    "DataError",
    "DataSource",
    "YahooSource",
    "bar_spacing_minutes",
    "closed_bars",
    "create_source",
    "history_path",
    "load_csv",
    "merge_into_csv",
    "normalize_bars",
    "resample_bars",
]
