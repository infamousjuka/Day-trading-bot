import math

import pytest

from daytrading_bot.config import RiskConfig
from daytrading_bot.risk import CostModel, plan_trade

COSTS = CostModel(fee_per_order=1.0, spread_pct=0.05, slippage_pct=0.02)


def test_cost_model_consistency():
    entry, exit_, qty = 100.0, 102.0, 10
    net = COSTS.net_pnl(entry, exit_, qty)
    via_prices = (COSTS.sell_price(exit_) - COSTS.buy_price(entry)) * qty - 2 * COSTS.fee_per_order
    assert net == pytest.approx(via_prices)
    assert COSTS.trade_costs(entry, exit_, qty) == pytest.approx(20.0 - net)


def test_plan_respects_risk_budget_and_caps():
    risk = RiskConfig(risk_per_trade=50, max_position_value=100_000, min_reward_cost_ratio=0)
    plan, why = plan_trade(100.0, 0.5, risk, COSTS, available_capital=100_000)
    assert plan is not None, why
    assert plan.stop == pytest.approx(99.0)
    assert plan.take_profit == pytest.approx(102.0)
    assert plan.qty == math.floor(plan.qty)
    assert plan.risk_eur <= 50.0 + 1e-6
    capped, _ = plan_trade(100.0, 0.5, RiskConfig(max_position_value=1000, min_reward_cost_ratio=0), COSTS, 100_000)
    assert capped.qty == 10
    low_cash, _ = plan_trade(100.0, 0.5, RiskConfig(min_reward_cost_ratio=0), COSTS, available_capital=550)
    assert low_cash.qty == 5


def test_plan_rejects_expensive_share():
    plan, why = plan_trade(5000.0, 10.0, RiskConfig(max_position_value=2500), COSTS, 10_000)
    assert plan is None and "unter 1 Stück" in why


def test_fractional_shares():
    plan, _ = plan_trade(
        5000.0, 10.0, RiskConfig(max_position_value=2500, whole_shares=False, min_reward_cost_ratio=0), COSTS, 10_000
    )
    assert plan is not None and plan.qty == pytest.approx(0.5)


def test_plan_rejects_when_costs_eat_the_chance():
    plan, why = plan_trade(100.0, 0.02, RiskConfig(min_reward_cost_ratio=3.0), COSTS, 10_000)
    assert plan is None and "Kosten" in why


def test_limit_buffer():
    plan, _ = plan_trade(100.0, 0.5, RiskConfig(limit_buffer_pct=0.1, min_reward_cost_ratio=0), COSTS, 10_000)
    assert plan.limit == pytest.approx(100.1)
