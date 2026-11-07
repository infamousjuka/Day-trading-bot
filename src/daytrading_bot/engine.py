"""Signal-Engine: verbindet Strategie, Risiko, Handelszeiten und Positionen.

Dieselbe Engine laeuft live (Telegram) und im Backtest. Sie bewertet immer
eine *abgeschlossene* Kerze und liefert Kauf- oder Verkaufs-Signale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from .config import Instrument, RiskConfig
from .market_hours import MarketCalendar
from .portfolio import ClosedTrade, Portfolio
from .risk import CostModel, TradePlan, plan_trade
from .strategy import EmaCrossStrategy

log = logging.getLogger(__name__)

BUY = "BUY"
SELL = "SELL"

# Ausstiegsgruende (Codes fuer Statistik, Texte fuer Nachrichten)
EXIT_STOP = "stop"
EXIT_TARGET = "target"
EXIT_SIGNAL = "signal"
EXIT_EOD = "eod"
EXIT_OVERNIGHT = "overnight"


@dataclass
class Signal:
    action: str
    instrument: Instrument
    bar_start: datetime
    bar_end: datetime
    price: float
    qty: float
    reason: str
    reason_code: str
    position_id: str
    plan: TradePlan | None = None
    trade: ClosedTrade | None = None
    stop: float | None = None
    take_profit: float | None = None
    rsi: float | None = None
    delay_minutes: float | None = None

    @property
    def symbol(self) -> str:
        return self.instrument.symbol


class SignalEngine:
    def __init__(
        self,
        strategy: EmaCrossStrategy,
        calendar: MarketCalendar,
        portfolio: Portfolio,
        risk: RiskConfig,
        costs: CostModel,
        interval_minutes: int,
    ) -> None:
        self.strategy = strategy
        self.calendar = calendar
        self.portfolio = portfolio
        self.risk = risk
        self.costs = costs
        self.interval = timedelta(minutes=interval_minutes)
        self.last_rejection: dict[str, str] = {}
        self.rejections: dict[str, int] = {}

    def daily_limit_hit(self, day) -> bool:
        return self.portfolio.realized_on(day) <= -self.risk.max_daily_loss

    def on_bar(
        self,
        instrument: Instrument,
        df: pd.DataFrame,
        i: int,
        *,
        allow_entries: bool = True,
    ) -> list[Signal]:
        """Kerze ``i`` (bereits mit Indikatoren, siehe Strategy.prepare) bewerten.

        Offene Positionen werden auf Ausstieg geprueft; ohne Position wird ein
        Einstieg gesucht. Das Portfolio wird direkt mit dem Schlusskurs der
        Kerze als geschaetztem Ausfuehrungskurs aktualisiert.
        """
        bar_start = self.calendar.local(df.index[i].to_pydatetime())
        bar_end = bar_start + self.interval
        close = float(df["close"].iat[i])
        symbol = instrument.symbol

        pos = self.portfolio.positions.get(symbol)
        if pos is not None:
            exit_ = self._exit_reason(df, i, pos, bar_start, bar_end)
            if exit_ is None:
                return []
            code, reason = exit_
            trade = self.portfolio.close_position(symbol, close, bar_end, reason, code, self.costs)
            return [
                Signal(
                    action=SELL,
                    instrument=instrument,
                    bar_start=bar_start,
                    bar_end=bar_end,
                    price=close,
                    qty=trade.qty,
                    reason=reason,
                    reason_code=code,
                    position_id=trade.id,
                    trade=trade,
                    stop=pos.stop,
                    take_profit=pos.take_profit,
                )
            ]

        if not allow_entries or not self.calendar.in_entry_window(bar_end):
            return []
        if len(self.portfolio.positions) >= self.risk.max_open_positions:
            return []
        if self.daily_limit_hit(bar_end.date()):
            return []

        reason = self.strategy.entry_reason(df, i)
        if reason is None:
            return []
        atr_value = float(df["atr"].iat[i])
        plan, why_not = plan_trade(
            close, atr_value, self.risk, self.costs, self.portfolio.available_capital()
        )
        if plan is None:
            self.last_rejection[symbol] = why_not
            key = why_not.split(" (")[0]
            self.rejections[key] = self.rejections.get(key, 0) + 1
            log.info("%s: Signal verworfen um %s (%s)", symbol, bar_end.strftime("%H:%M"), why_not)
            return []

        pos = self.portfolio.open_position(
            symbol, instrument.name, plan.qty, close, bar_end, plan.stop, plan.take_profit
        )
        return [
            Signal(
                action=BUY,
                instrument=instrument,
                bar_start=bar_start,
                bar_end=bar_end,
                price=close,
                qty=plan.qty,
                reason=reason,
                reason_code="entry",
                position_id=pos.id,
                plan=plan,
                stop=plan.stop,
                take_profit=plan.take_profit,
                rsi=float(df["rsi"].iat[i]),
            )
        ]

    def _exit_reason(
        self,
        df: pd.DataFrame,
        i: int,
        pos,
        bar_start: datetime,
        bar_end: datetime,
    ) -> tuple[str, str] | None:
        # Kerzen, die vor dem Einstieg endeten, nicht erneut bewerten.
        if bar_end <= pos.entry_time:
            return None
        if self.calendar.local(pos.entry_time).date() < bar_start.date() and self.calendar.cfg.flat_at:
            return EXIT_OVERNIGHT, "Position aus dem Vortag – glattstellen (Daytrading)"
        low = float(df["low"].iat[i])
        high = float(df["high"].iat[i])
        # Liegen Stop und Ziel in derselben Kerze, ist die Reihenfolge unbekannt:
        # vorsichtshalber zaehlt der Stop.
        if low <= pos.stop:
            return EXIT_STOP, "Stop-Loss erreicht"
        if high >= pos.take_profit:
            return EXIT_TARGET, "Kursziel erreicht"
        strategy_exit = self.strategy.exit_reason(df, i)
        if strategy_exit:
            return EXIT_SIGNAL, strategy_exit
        if self.calendar.must_be_flat(bar_end):
            return EXIT_EOD, "Handelsschluss naht – Position glattstellen (Daytrading)"
        return None
