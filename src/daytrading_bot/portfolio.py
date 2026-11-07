"""Virtuelle Positionen, die aus den Signalen entstehen, und ihre Speicherung.

Der Bot kann nicht sehen, was im Trade-Republic-Depot passiert. Er nimmt an,
dass jedes Kauf-Signal ausgefuehrt wurde, bis du es per Knopf oder /schliessen
austraegst. Daraus entstehen die Verkaufs-Signale.
"""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .risk import CostModel

MAX_STORED_TRADES = 200


@dataclass
class Position:
    id: str
    symbol: str
    name: str
    qty: float
    entry_price: float
    entry_time: datetime
    stop: float
    take_profit: float
    confirmed: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["entry_time"] = self.entry_time.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Position":
        d = dict(d)
        d["entry_time"] = datetime.fromisoformat(d["entry_time"])
        return cls(**d)


@dataclass
class ClosedTrade:
    id: str
    symbol: str
    name: str
    qty: float
    entry_price: float
    entry_time: datetime
    exit_price: float
    exit_time: datetime
    reason: str
    reason_code: str
    pnl_net: float
    costs: float

    @property
    def pnl_pct(self) -> float:
        invested = self.entry_price * self.qty
        return 100.0 * self.pnl_net / invested if invested else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["entry_time"] = self.entry_time.isoformat()
        d["exit_time"] = self.exit_time.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ClosedTrade":
        d = dict(d)
        d["entry_time"] = datetime.fromisoformat(d["entry_time"])
        d["exit_time"] = datetime.fromisoformat(d["exit_time"])
        return cls(**d)


@dataclass
class Portfolio:
    capital: float
    positions: dict[str, Position] = field(default_factory=dict)
    realized: dict[str, float] = field(default_factory=dict)
    trades: list[ClosedTrade] = field(default_factory=list)
    last_bar: dict[str, str] = field(default_factory=dict)
    last_price: dict[str, tuple[float, str]] = field(default_factory=dict)
    flags: dict[str, str] = field(default_factory=dict)
    paused: bool = False

    # ------------------------------------------------------------------ #
    def open_position(
        self,
        symbol: str,
        name: str,
        qty: float,
        price: float,
        time: datetime,
        stop: float,
        take_profit: float,
    ) -> Position:
        if symbol in self.positions:
            raise ValueError(f"Für {symbol} ist schon eine Position offen")
        pos = Position(
            id=uuid.uuid4().hex[:10],
            symbol=symbol,
            name=name,
            qty=qty,
            entry_price=price,
            entry_time=time,
            stop=stop,
            take_profit=take_profit,
        )
        self.positions[symbol] = pos
        return pos

    def close_position(
        self,
        symbol: str,
        price: float,
        time: datetime,
        reason: str,
        reason_code: str,
        costs: CostModel,
    ) -> ClosedTrade:
        pos = self.positions.pop(symbol)
        trade = ClosedTrade(
            id=pos.id,
            symbol=pos.symbol,
            name=pos.name,
            qty=pos.qty,
            entry_price=pos.entry_price,
            entry_time=pos.entry_time,
            exit_price=price,
            exit_time=time,
            reason=reason,
            reason_code=reason_code,
            pnl_net=costs.net_pnl(pos.entry_price, price, pos.qty),
            costs=costs.trade_costs(pos.entry_price, price, pos.qty),
        )
        self._book(trade.exit_time.date(), trade.pnl_net)
        self.trades.append(trade)
        if len(self.trades) > MAX_STORED_TRADES:
            del self.trades[: len(self.trades) - MAX_STORED_TRADES]
        return trade

    def reprice_trade(self, trade: ClosedTrade, exit_price: float, costs: CostModel) -> None:
        """Ausstiegskurs nachtraeglich korrigieren (Backtest: Ausfuehrung zur naechsten Kerze)."""
        new_pnl = costs.net_pnl(trade.entry_price, exit_price, trade.qty)
        self._book(trade.exit_time.date(), new_pnl - trade.pnl_net)
        trade.exit_price = exit_price
        trade.pnl_net = new_pnl
        trade.costs = costs.trade_costs(trade.entry_price, exit_price, trade.qty)

    def drop_position(self, symbol: str) -> Position | None:
        """Position ohne Ergebnis austragen (nicht gekauft / selbst verkauft)."""
        return self.positions.pop(symbol, None)

    def find(self, key: str) -> Position | None:
        """Position ueber Symbol, Name oder ID finden (Gross-/Kleinschreibung egal)."""
        key_l = key.strip().lower()
        for pos in self.positions.values():
            if key_l in {pos.symbol.lower(), pos.name.lower(), pos.id.lower()}:
                return pos
            if pos.symbol.lower().split(".")[0] == key_l:
                return pos
        return None

    def _book(self, day: date, amount: float) -> None:
        key = day.isoformat()
        self.realized[key] = self.realized.get(key, 0.0) + amount

    def realized_on(self, day: date) -> float:
        return self.realized.get(day.isoformat(), 0.0)

    def trades_on(self, day: date) -> list[ClosedTrade]:
        return [t for t in self.trades if t.exit_time.date() == day]

    def invested(self) -> float:
        return sum(p.qty * p.entry_price for p in self.positions.values())

    def available_capital(self) -> float:
        return self.capital + sum(self.realized.values()) - self.invested()

    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "capital": self.capital,
            "paused": self.paused,
            "positions": {k: p.to_dict() for k, p in self.positions.items()},
            "realized": self.realized,
            "trades": [t.to_dict() for t in self.trades],
            "last_bar": self.last_bar,
            "last_price": {k: list(v) for k, v in self.last_price.items()},
            "flags": self.flags,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any], capital: float | None = None) -> "Portfolio":
        return cls(
            capital=float(capital if capital is not None else d.get("capital", 0.0)),
            positions={k: Position.from_dict(v) for k, v in d.get("positions", {}).items()},
            realized={k: float(v) for k, v in d.get("realized", {}).items()},
            trades=[ClosedTrade.from_dict(t) for t in d.get("trades", [])],
            last_bar=dict(d.get("last_bar", {})),
            last_price={k: (float(v[0]), str(v[1])) for k, v in d.get("last_price", {}).items()},
            flags=dict(d.get("flags", {})),
            paused=bool(d.get("paused", False)),
        )


class StateStore:
    """Speichert das Portfolio als JSON, damit ein Neustart nichts vergisst."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self, capital: float) -> Portfolio:
        if not self.path.exists():
            return Portfolio(capital=capital)
        with self.path.open(encoding="utf-8") as fh:
            return Portfolio.from_dict(json.load(fh), capital=capital)

    def save(self, portfolio: Portfolio) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(portfolio.to_dict(), fh, ensure_ascii=False, indent=2)
        tmp.replace(self.path)


JOURNAL_FIELDS = [
    "id",
    "symbol",
    "name",
    "qty",
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "reason_code",
    "reason",
    "costs",
    "pnl_net",
]


def append_journal(path: str | Path, trades: list[ClosedTrade]) -> None:
    """Abgeschlossene (Signal-)Trades an das Trade-Journal (CSV) anhaengen."""
    if not trades:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=JOURNAL_FIELDS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        for trade in trades:
            row = trade.to_dict()
            row["costs"] = round(trade.costs, 2)
            row["pnl_net"] = round(trade.pnl_net, 2)
            writer.writerow(row)
