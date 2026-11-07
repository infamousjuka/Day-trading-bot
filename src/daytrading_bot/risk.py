"""Kostenmodell (Gebuehren, Spread, Slippage) und Positionsgroesse."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import CostConfig, RiskConfig


@dataclass(frozen=True)
class CostModel:
    """Rechnet Ordergebuehren, halbe Geld-Brief-Spanne und Slippage ein."""

    fee_per_order: float
    spread_pct: float
    slippage_pct: float

    @classmethod
    def from_config(cls, cfg: CostConfig) -> "CostModel":
        return cls(cfg.fee_per_order, cfg.spread_pct, cfg.slippage_pct)

    @property
    def side_fraction(self) -> float:
        """Preisnachteil je Order als Anteil (halber Spread + Slippage)."""
        return (self.spread_pct / 2.0 + self.slippage_pct) / 100.0

    def buy_price(self, reference: float) -> float:
        return reference * (1.0 + self.side_fraction)

    def sell_price(self, reference: float) -> float:
        return reference * (1.0 - self.side_fraction)

    def trade_costs(self, entry: float, exit_: float, qty: float) -> float:
        """Gesamtkosten eines Round Trips (Kauf + Verkauf) in EUR."""
        return 2.0 * self.fee_per_order + qty * (entry + exit_) * self.side_fraction

    def gross_pnl(self, entry: float, exit_: float, qty: float) -> float:
        return (exit_ - entry) * qty

    def net_pnl(self, entry: float, exit_: float, qty: float) -> float:
        """Ergebnis nach Kosten (vor Steuern)."""
        return self.gross_pnl(entry, exit_, qty) - self.trade_costs(entry, exit_, qty)


@dataclass(frozen=True)
class TradePlan:
    qty: float
    entry: float
    stop: float
    take_profit: float
    limit: float
    risk_eur: float
    reward_eur: float
    costs_eur: float

    @property
    def position_value(self) -> float:
        return self.qty * self.entry


def _round_qty(qty: float, whole_shares: bool) -> float:
    if whole_shares:
        return float(math.floor(qty + 1e-9))
    return math.floor(qty * 1000 + 1e-6) / 1000


def plan_trade(
    price: float,
    atr_value: float,
    risk: RiskConfig,
    costs: CostModel,
    available_capital: float,
) -> tuple[TradePlan | None, str]:
    """Stop-Loss, Kursziel und Stueckzahl fuer einen Kauf berechnen.

    Die Stueckzahl wird so gewaehlt, dass ein Stop-Loss inklusive Kosten
    hoechstens ``risk.risk_per_trade`` EUR kostet. Gibt ``(None, Grund)`` zurueck,
    wenn der Trade nicht sinnvoll ist.
    """
    if not math.isfinite(price) or price <= 0:
        return None, "kein gültiger Kurs"
    if not math.isfinite(atr_value) or atr_value <= 0:
        return None, "ATR nicht verfügbar"

    stop = price - risk.stop_loss_atr * atr_value
    take_profit = price + risk.take_profit_atr * atr_value
    if stop <= 0:
        return None, "Stop-Loss läge unter 0"

    budget = risk.risk_per_trade - 2.0 * costs.fee_per_order
    if budget <= 0:
        return None, "Risikobudget kleiner als die Ordergebühren"
    per_share_risk = (price - stop) + 2.0 * price * costs.side_fraction
    qty = budget / per_share_risk
    qty = min(qty, risk.max_position_value / price, max(available_capital, 0.0) / price)
    qty = _round_qty(qty, risk.whole_shares)
    if qty <= 0:
        return None, "Positionsgröße unter 1 Stück (Kapital oder Risikobudget zu klein)"

    costs_eur = costs.trade_costs(price, price, qty)
    risk_eur = (price - stop) * qty + costs_eur
    reward_gross = (take_profit - price) * qty
    if reward_gross < risk.min_reward_cost_ratio * costs_eur:
        return None, (
            f"Chance zu klein gegenüber Kosten (Chance {reward_gross:.2f} EUR, "
            f"Kosten {costs_eur:.2f} EUR)"
        )
    limit = price * (1.0 + risk.limit_buffer_pct / 100.0)
    return (
        TradePlan(
            qty=qty,
            entry=price,
            stop=stop,
            take_profit=take_profit,
            limit=limit,
            risk_eur=risk_eur,
            reward_eur=reward_gross - costs_eur,
            costs_eur=costs_eur,
        ),
        "",
    )
