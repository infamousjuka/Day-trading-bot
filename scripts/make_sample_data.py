"""Erzeugt SYNTHETISCHE 5-Minuten-Kerzen fuer Tests und einen ersten Backtest.

Die Daten sind ein Zufallspfad mit wechselnden Trendphasen -- KEINE echten
Kurse. Sie dienen nur dazu, den Bot ohne Internet auszuprobieren.

    python scripts/make_sample_data.py
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from daytrading_bot.data.csv_source import save_csv  # noqa: E402
from daytrading_bot.market_hours import xetra_holidays  # noqa: E402

TZ = ZoneInfo("Europe/Berlin")


def synthetic_bars(
    start: date,
    days: int,
    seed: int = 42,
    start_price: float = 100.0,
    interval_minutes: int = 5,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    price = start_price
    day = start
    produced = 0
    drift = 0.0
    while produced < days:
        if day.weekday() < 5 and day not in xetra_holidays(day.year):
            price *= 1 + rng.normal(0, 0.004)  # Kursluecke ueber Nacht
            moment = datetime.combine(day, time(9, 0), tzinfo=TZ)
            end = datetime.combine(day, time(17, 30), tzinfo=TZ)
            while moment < end:
                if rng.random() < 1 / 25:
                    drift = rng.choice([-0.0007, -0.0003, 0.0, 0.0003, 0.0007])
                ret = drift + rng.normal(0, 0.0015)
                open_ = price
                close = open_ * (1 + ret)
                high = max(open_, close) * (1 + abs(rng.normal(0, 0.0007)))
                low = min(open_, close) * (1 - abs(rng.normal(0, 0.0007)))
                volume = float(rng.integers(2_000, 20_000))
                rows.append((moment, open_, high, low, close, volume))
                price = close
                moment += timedelta(minutes=interval_minutes)
            produced += 1
        day += timedelta(days=1)
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.set_index(pd.DatetimeIndex(df["timestamp"])).drop(columns="timestamp")
    return df.round({"open": 4, "high": 4, "low": 4, "close": 4})


if __name__ == "__main__":
    bars = synthetic_bars(date(2026, 10, 5), days=20)
    out = ROOT / "data" / "sample" / "SYNTH_5m.csv"
    save_csv(bars, out)
    print(f"{len(bars)} synthetische Kerzen -> {out}")
