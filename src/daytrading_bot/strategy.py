"""Handelsstrategie: EMA-Kreuzung mit RSI- und VWAP-Filter (nur Long).

Hypothese: Kreuzt der kurze EMA den langen von unten, beginnt oft eine kurze
Aufwaertsbewegung. Der RSI-Korridor filtert schwache und bereits ueberhitzte
Bewegungen heraus, der VWAP-Filter verlangt, dass der Kurs ueber dem
Durchschnittspreis des Tages liegt. Ob das nach Kosten Geld verdient, muss der
Backtest zeigen -- es gibt keine Garantie.

Trade Republic erlaubt keine Leerverkaeufe, daher gibt es nur Kauf-Signale zum
Einstieg und Verkaufs-Signale zum Ausstieg.
"""

from __future__ import annotations

import math

import pandas as pd

from .config import StrategyConfig
from .indicators import atr, ema, intraday_vwap, rsi


class EmaCrossStrategy:
    name = "EMA-Kreuzung mit RSI- und VWAP-Filter"

    def __init__(self, cfg: StrategyConfig) -> None:
        self.cfg = cfg

    @property
    def warmup_bars(self) -> int:
        """Mindestanzahl Kerzen, bevor die Indikatoren belastbar sind."""
        return max(self.cfg.ema_slow, self.cfg.rsi_period, self.cfg.atr_period) + 2

    def prepare(self, bars: pd.DataFrame) -> pd.DataFrame:
        """Indikatoren und Kreuzungs-Signale berechnen."""
        c = self.cfg
        df = bars.copy()
        df["ema_fast"] = ema(df["close"], c.ema_fast)
        df["ema_slow"] = ema(df["close"], c.ema_slow)
        df["rsi"] = rsi(df["close"], c.rsi_period)
        df["atr"] = atr(df, c.atr_period)
        df["vwap"] = intraday_vwap(df)

        valid = (
            df["ema_fast"].notna()
            & df["ema_slow"].notna()
            & df["ema_fast"].shift(1).notna()
            & df["ema_slow"].shift(1).notna()
        )
        above = df["ema_fast"] > df["ema_slow"]
        below = df["ema_fast"] < df["ema_slow"]
        prev_above = above.shift(1, fill_value=False).astype(bool)
        prev_below = below.shift(1, fill_value=False).astype(bool)
        df["cross_up"] = valid & above & ~prev_above
        df["cross_down"] = valid & below & ~prev_below
        return df

    def entry_reason(self, df: pd.DataFrame, i: int) -> str | None:
        """Begruendung fuer ein Kauf-Signal auf Kerze ``i`` oder None."""
        c = self.cfg
        if i < self.warmup_bars or not bool(df["cross_up"].iat[i]):
            return None
        rsi_value = float(df["rsi"].iat[i])
        atr_value = float(df["atr"].iat[i])
        if math.isnan(rsi_value) or math.isnan(atr_value) or atr_value <= 0:
            return None
        if not c.rsi_min <= rsi_value <= c.rsi_max:
            return None
        parts = [f"EMA {c.ema_fast} kreuzt EMA {c.ema_slow} nach oben", f"RSI {rsi_value:.0f}"]
        if c.use_vwap_filter:
            vwap_value = float(df["vwap"].iat[i])
            if not math.isnan(vwap_value):
                if float(df["close"].iat[i]) <= vwap_value:
                    return None
                parts.append("Kurs über Tages-VWAP")
        return " · ".join(parts)

    def exit_reason(self, df: pd.DataFrame, i: int) -> str | None:
        """Strategie-Ausstieg (zusaetzlich zu Stop-Loss/Kursziel/Tagesende)."""
        c = self.cfg
        if c.exit_on_cross_down and i >= 1 and bool(df["cross_down"].iat[i]):
            return f"EMA {c.ema_fast} kreuzt EMA {c.ema_slow} nach unten"
        return None
