"""Ein Pruefdurchlauf: Kurse holen, neue Kerzen bewerten, Signale sammeln."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from .config import Config
from .data import DataSource, closed_bars, history_path, merge_into_csv
from .engine import SignalEngine, Signal
from .market_hours import MarketCalendar
from .portfolio import ClosedTrade, Portfolio
from .strategy import EmaCrossStrategy

log = logging.getLogger(__name__)

# Nach einer Pause (z. B. Neustart) hoechstens so viele verpasste Kerzen nachholen.
MAX_CATCHUP_BARS = 6


@dataclass
class ScanReport:
    time: datetime
    signals: list[Signal] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    stale: dict[str, float] = field(default_factory=dict)
    checked: int = 0
    notices: list[str] = field(default_factory=list)
    snapshots: dict[str, dict] = field(default_factory=dict)

    @property
    def closed_trades(self) -> list[ClosedTrade]:
        return [s.trade for s in self.signals if s.trade is not None]


class Scanner:
    def __init__(
        self,
        cfg: Config,
        source: DataSource,
        strategy: EmaCrossStrategy,
        engine: SignalEngine,
        portfolio: Portfolio,
        calendar: MarketCalendar,
    ) -> None:
        self.cfg = cfg
        self.source = source
        self.strategy = strategy
        self.engine = engine
        self.portfolio = portfolio
        self.calendar = calendar

    # Blockierender Teil (Netzwerk) -> im Bot in einem eigenen Thread.
    def fetch_all(self) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        data: dict[str, pd.DataFrame] = {}
        errors: dict[str, str] = {}
        for inst in self.cfg.watchlist:
            try:
                df = self.source.fetch(inst.symbol, self.cfg.data.history_days)
            except Exception as exc:  # Netzwerk, Yahoo-Aenderungen, ...
                log.warning("Kurse fuer %s nicht geladen: %s", inst.symbol, exc)
                errors[inst.symbol] = str(exc)
                continue
            data[inst.symbol] = df
        return data, errors

    def record(self, data: dict[str, pd.DataFrame], now: datetime) -> None:
        """Abgeschlossene Kerzen in data/history mitschreiben (eigener Datensatz)."""
        if not self.cfg.data.record_bars or self.cfg.data.source != "yahoo":
            return
        directory = self.cfg.path(self.cfg.data.csv_dir)
        for symbol, df in data.items():
            bars = closed_bars(df, now, self.cfg.interval_minutes)
            if bars.empty:
                continue
            try:
                merge_into_csv(
                    bars,
                    history_path(directory, symbol, self.cfg.interval_minutes),
                    self.cfg.market.timezone,
                )
            except Exception as exc:
                log.warning("Kerzen fuer %s nicht gespeichert: %s", symbol, exc)

    # Nicht blockierender Teil -> veraendert das Portfolio.
    def process(
        self,
        data: dict[str, pd.DataFrame],
        now: datetime,
        errors: dict[str, str] | None = None,
    ) -> ScanReport:
        now = self.calendar.local(now)
        report = ScanReport(time=now, errors=dict(errors or {}))
        interval = timedelta(minutes=self.cfg.interval_minutes)
        max_delay = self.cfg.data.max_delay_minutes

        for inst in self.cfg.watchlist:
            df = data.get(inst.symbol)
            if df is None:
                continue
            bars = closed_bars(df, now, self.cfg.interval_minutes)
            if bars.empty:
                continue
            report.checked += 1
            prepared = self.strategy.prepare(bars)
            last_ts = prepared.index[-1]
            last_end = last_ts.to_pydatetime() + interval
            self.portfolio.last_price[inst.symbol] = (
                float(prepared["close"].iat[-1]),
                last_end.isoformat(),
            )
            report.snapshots[inst.symbol] = {
                "time": last_end,
                "close": float(prepared["close"].iat[-1]),
                "ema_fast": float(prepared["ema_fast"].iat[-1]),
                "ema_slow": float(prepared["ema_slow"].iat[-1]),
                "rsi": float(prepared["rsi"].iat[-1]),
                "atr": float(prepared["atr"].iat[-1]),
                "vwap": float(prepared["vwap"].iat[-1]),
            }
            delay = (now - last_end).total_seconds() / 60.0
            if delay > max_delay and self.calendar.is_open(now):
                report.stale[inst.symbol] = delay

            done = self.portfolio.last_bar.get(inst.symbol)
            if done is None:
                todo = [len(prepared) - 1]
            else:
                done_ts = pd.Timestamp(done)
                todo = [i for i in range(len(prepared)) if prepared.index[i] > done_ts]
                todo = todo[-MAX_CATCHUP_BARS:]

            for i in todo:
                bar_end = prepared.index[i].to_pydatetime() + interval
                bar_delay = (now - bar_end).total_seconds() / 60.0
                allow_entries = not self.portfolio.paused and bar_delay <= max_delay
                for sig in self.engine.on_bar(inst, prepared, i, allow_entries=allow_entries):
                    sig.delay_minutes = bar_delay
                    report.signals.append(sig)
            self.portfolio.last_bar[inst.symbol] = last_ts.isoformat()

        day = now.date()
        # Merker vergangener Tage aufraeumen
        self.portfolio.flags = {
            k: v for k, v in self.portfolio.flags.items() if k.endswith(day.isoformat())
        }
        flag = f"daily_limit:{day.isoformat()}"
        if self.engine.daily_limit_hit(day) and flag not in self.portfolio.flags:
            self.portfolio.flags[flag] = "1"
            report.notices.append(
                "🛑 Tagesverlust-Limit erreicht – heute keine neuen Kauf-Signale mehr. "
                "Verkaufs-Signale für offene Positionen kommen weiterhin."
            )
        return report

    def scan(self, now: datetime | None = None) -> ScanReport:
        """Kompletter Durchlauf (blockierend) – fuer Konsole und Tests."""
        data, errors = self.fetch_all()
        now = now or self.calendar.now()
        self.record(data, now)
        return self.process(data, now, errors)
