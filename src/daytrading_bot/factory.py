"""Alle Bausteine aus der Konfiguration zusammensetzen."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .data import DataSource, create_source
from .engine import SignalEngine
from .market_hours import MarketCalendar
from .portfolio import Portfolio, StateStore
from .risk import CostModel
from .scanner import Scanner
from .strategy import EmaCrossStrategy


@dataclass
class Components:
    cfg: Config
    calendar: MarketCalendar
    strategy: EmaCrossStrategy
    costs: CostModel
    portfolio: Portfolio
    engine: SignalEngine
    source: DataSource
    scanner: Scanner
    store: StateStore


def build(
    cfg: Config,
    *,
    portfolio: Portfolio | None = None,
    source: DataSource | None = None,
) -> Components:
    calendar = MarketCalendar(cfg.market)
    strategy = EmaCrossStrategy(cfg.strategy)
    costs = CostModel.from_config(cfg.costs)
    store = StateStore(cfg.path(cfg.files.state))
    if portfolio is None:
        portfolio = store.load(cfg.risk.capital)
    engine = SignalEngine(strategy, calendar, portfolio, cfg.risk, costs, cfg.interval_minutes)
    source = source or create_source(cfg)
    scanner = Scanner(cfg, source, strategy, engine, portfolio, calendar)
    return Components(cfg, calendar, strategy, costs, portfolio, engine, source, scanner, store)
