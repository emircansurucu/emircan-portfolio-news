from __future__ import annotations

from datetime import date

import pytest

from investment_agent.calculations import (
    annualized_volatility,
    convert_usd_to_try,
    external_cash_flows,
    investment_return,
    maximum_drawdown,
    value_portfolio,
    weighted_contribution,
    xirr,
)
from investment_agent.models import Transaction
from tests.conftest import make_quote


def test_portfolio_valuation_and_currency(portfolio, now):
    quotes = {"AAA": make_quote("AAA", 100, 90, now), "GOLD": make_quote("GOLD", 50, 50, now)}
    snapshot = value_portfolio(portfolio, quotes, 32, now)
    assert snapshot.total_value_usd == pytest.approx(700)
    assert snapshot.total_value_try == pytest.approx(22_400)
    assert sum(position.weight for position in snapshot.positions) == pytest.approx(1)


def test_usd_try_conversion_rejects_invalid_rate():
    assert convert_usd_to_try(100, 32.5) == 3250
    with pytest.raises(ValueError):
        convert_usd_to_try(100, 0)


def test_weighted_contribution():
    assert weighted_contribution(2, 110, 100, 1000) == pytest.approx(0.02)


def test_cash_flow_separation():
    transactions = [
        Transaction(date=date(2026, 1, 1), type="deposit", amount_usd=1000),
        Transaction(date=date(2026, 1, 2), type="buy", amount_usd=800),
        Transaction(date=date(2026, 1, 3), type="dividend", amount_usd=10),
        Transaction(date=date(2026, 1, 4), type="withdrawal", amount_usd=100),
    ]
    assert external_cash_flows(transactions) == 900
    assert investment_return(1000, 1200, 100) == pytest.approx(0.1)


def test_xirr():
    result = xirr([(date(2025, 1, 1), -1000), (date(2026, 1, 1), 1100)])
    assert result == pytest.approx(0.1, abs=1e-6)
    assert xirr([(date(2025, 1, 1), 100), (date(2026, 1, 1), 110)]) is None
    assert xirr([(date(2025, 1, 1), -100), (date(2025, 1, 1), 110)]) is None


def test_maximum_drawdown():
    assert maximum_drawdown([100, 120, 90, 110]) == pytest.approx(-0.25)
    assert annualized_volatility([100, 101, 99, 103]) > 0
