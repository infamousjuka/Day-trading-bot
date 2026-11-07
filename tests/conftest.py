from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from daytrading_bot.config import Config, FilesConfig, Instrument  # noqa: E402

TZ = ZoneInfo("Europe/Berlin")


def berlin(y, m, d, hh, mm, ss=0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=TZ)


def make_bars(
    closes,
    start: datetime | None = None,
    spread: float = 0.05,
    interval: int = 5,
    volume: float = 1000.0,
) -> pd.DataFrame:
    """Kerzen aus Schlusskursen bauen (open = vorheriger Schluss)."""
    start = start or berlin(2026, 11, 2, 9, 0)
    rows = []
    prev = closes[0]
    for k, close in enumerate(closes):
        open_ = prev
        rows.append(
            {
                "open": open_,
                "high": max(open_, close) + spread,
                "low": min(open_, close) - spread,
                "close": close,
                "volume": volume,
            }
        )
        prev = close
    index = pd.DatetimeIndex([start + timedelta(minutes=interval * k) for k in range(len(closes))])
    df = pd.DataFrame(rows, index=index)
    df.index.name = "timestamp"
    return df


@pytest.fixture
def cfg(tmp_path) -> Config:
    base = Config(watchlist=(Instrument("TEST.DE", "Test AG"), Instrument("OTHER.DE", "Other")))
    return replace(
        base,
        base_dir=tmp_path,
        files=FilesConfig(
            state="state/state.json",
            journal="state/journal.csv",
            log="logs/bot.log",
            reports="reports",
        ),
    )


@pytest.fixture
def sample_csv() -> Path:
    return ROOT / "data" / "sample" / "SYNTH_5m.csv"


class StubStrategy:
    """Strategie-Attrappe: Einstiege/Ausstiege auf festgelegten Kerzen."""

    warmup_bars = 0

    def __init__(self, entries=(), exits=(), atr=0.5):
        self.entries = set(entries)
        self.exits = set(exits)
        self.atr = atr

    def prepare(self, df):
        out = df.copy()
        out["atr"] = self.atr
        out["rsi"] = 60.0
        out["ema_fast"] = out["close"]
        out["ema_slow"] = out["close"]
        out["vwap"] = out["close"]
        return out

    def entry_reason(self, df, i):
        return "Test-Einstieg" if i in self.entries else None

    def exit_reason(self, df, i):
        return "Test-Ausstieg" if i in self.exits else None


class FakeSource:
    def __init__(self, frames: dict[str, pd.DataFrame] | None = None, fail: set[str] | None = None):
        self.frames = frames or {}
        self.fail = fail or set()
        self.calls = 0

    def fetch(self, symbol, days):
        self.calls += 1
        if symbol in self.fail or symbol not in self.frames:
            raise RuntimeError(f"keine Daten für {symbol}")
        return self.frames[symbol]
