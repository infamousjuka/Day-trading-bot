from datetime import timedelta

import pandas as pd
import pytest

from conftest import berlin, make_bars
from daytrading_bot.data import (
    CsvSource,
    DataError,
    bar_spacing_minutes,
    closed_bars,
    load_csv,
    merge_into_csv,
    normalize_bars,
    resample_bars,
)
from daytrading_bot.data.csv_source import save_csv


def test_closed_bars_drops_running_and_off_grid_bars():
    df = make_bars([1.0, 2.0, 3.0, 4.0], start=berlin(2026, 11, 6, 10, 0))
    live_row = df.iloc[[-1]].copy()
    live_row.index = pd.DatetimeIndex([berlin(2026, 11, 6, 10, 17, 12)])
    df = pd.concat([df, live_row])
    out = closed_bars(df, berlin(2026, 11, 6, 10, 17, 30), 5)
    # 10:15-Kerze laeuft noch, 10:17:12 liegt nicht im Raster
    assert list(out.index) == [berlin(2026, 11, 6, 10, 0), berlin(2026, 11, 6, 10, 5), berlin(2026, 11, 6, 10, 10)]


def test_normalize_converts_timezone_and_columns():
    idx = pd.DatetimeIndex(pd.to_datetime(["2026-11-06 09:00", "2026-11-06 09:05"]).tz_localize("UTC"))
    raw = pd.DataFrame(
        {"Open": [1, 2], "High": [2, 3], "Low": [0.5, 1], "Close": [1.5, 2.5], "Volume": [10, None], "Adj Close": [1, 2]},
        index=idx,
    )
    out = normalize_bars(raw, "Europe/Berlin")
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert out.index[0] == berlin(2026, 11, 6, 10, 0)
    assert out["volume"].iloc[1] == 0.0


def test_csv_roundtrip_across_dst(tmp_path):
    # 25.10.2026: Umstellung von Sommer- auf Winterzeit
    a = make_bars([1.0, 2.0], start=berlin(2026, 10, 23, 9, 0))
    b = make_bars([3.0, 4.0], start=berlin(2026, 10, 26, 9, 0))
    df = pd.concat([a, b])
    path = tmp_path / "X_5m.csv"
    save_csv(df, path)
    text = path.read_text()
    assert "+0200" in text and "+0100" in text
    loaded = load_csv(path, "Europe/Berlin")
    assert list(loaded.index) == list(df.index)
    assert loaded["close"].tolist() == [1.0, 2.0, 3.0, 4.0]


def test_csv_without_timezone_is_local(tmp_path):
    path = tmp_path / "naive.csv"
    path.write_text("Datetime,Open,High,Low,Close,Volume\n2026-11-06 09:00:00,1,2,0.5,1.5,100\n")
    loaded = load_csv(path, "Europe/Berlin")
    assert loaded.index[0] == berlin(2026, 11, 6, 9, 0)


def test_csv_errors(tmp_path):
    with pytest.raises(DataError):
        load_csv(tmp_path / "fehlt.csv", "Europe/Berlin")
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n")
    with pytest.raises(DataError):
        load_csv(bad, "Europe/Berlin")


def test_merge_into_csv_deduplicates(tmp_path):
    path = tmp_path / "SAP.DE_5m.csv"
    df = make_bars([1.0, 2.0, 3.0])
    assert merge_into_csv(df.iloc[:2], path, "Europe/Berlin") == 2
    assert merge_into_csv(df.iloc[1:], path, "Europe/Berlin") == 3
    src = CsvSource(tmp_path, "Europe/Berlin", 5)
    assert len(src.fetch("SAP.DE", 5)) == 3


def test_resample_one_minute_to_five():
    df = make_bars([float(k) for k in range(1, 11)], interval=1, spread=0.0)
    assert bar_spacing_minutes(df) == 1.0
    out = resample_bars(df, 5)
    assert len(out) == 2
    first = out.iloc[0]
    assert first["open"] == 1.0 and first["close"] == 5.0
    assert first["high"] == 5.0 and first["low"] == 1.0
    assert first["volume"] == 5000.0
    assert out.index[1] - out.index[0] == timedelta(minutes=5)
