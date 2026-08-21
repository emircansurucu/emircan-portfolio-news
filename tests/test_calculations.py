from __future__ import annotations

from datetime import date

import pytest

from investment_agent.calculations import (
    annualized_volatility,
    convert_usd_to_try,
    external_cash_flows,
    history_metrics,
    investment_return,
    linked_modified_dietz_returns,
    maximum_drawdown,
    modified_dietz_return,
    quantity_change_warnings,
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


def test_monthly_cash_flows_and_purchase_do_not_create_performance():
    records = [
        {
            "as_of": "2026-01-01T21:00:00+00:00",
            "total_value_usd": 1000,
            "total_value_try": 40_000,
            "market_prices_usd": {"AAA": 100},
            "position_values_usd": {"AAA": 1000},
        },
        {
            "as_of": "2026-01-10T21:00:00+00:00",
            "total_value_usd": 1500,
            "total_value_try": 60_000,
            "market_prices_usd": {"AAA": 100},
            "position_values_usd": {"AAA": 1000},
        },
        {
            "as_of": "2026-01-20T21:00:00+00:00",
            "total_value_usd": 1500,
            "total_value_try": 60_000,
            "market_prices_usd": {"AAA": 100},
            "position_values_usd": {"AAA": 1500},
        },
        {
            "as_of": "2026-01-25T21:00:00+00:00",
            "total_value_usd": 1300,
            "total_value_try": 52_000,
            "market_prices_usd": {"AAA": 100},
            "position_values_usd": {"AAA": 1500},
        },
        {
            "as_of": "2026-01-31T21:00:00+00:00",
            "total_value_usd": 1300,
            "total_value_try": 52_000,
            "market_prices_usd": {"AAA": 100},
            "position_values_usd": {"AAA": 1500},
        },
    ]
    transactions = [
        Transaction(date=date(2026, 1, 10), type="deposit", amount_usd=500),
        Transaction(
            date=date(2026, 1, 20),
            type="buy",
            symbol="AAA",
            quantity=5,
            price_usd=100,
            amount_usd=500,
        ),
        Transaction(date=date(2026, 1, 25), type="withdrawal", amount_usd=200),
    ]
    returns = linked_modified_dietz_returns(records, transactions)
    assert returns == pytest.approx([0, 0, 0, 0])
    metrics = history_metrics(records, "monthly", transactions)
    assert metrics["investment_return_pct"] == pytest.approx(0)
    assert metrics["modified_dietz_return_pct"] == pytest.approx(0)
    assert metrics["maximum_drawdown_pct"] == pytest.approx(0)
    assert metrics["annualized_volatility_pct"] == pytest.approx(0)
    assert metrics["period_AAA_return_pct"] == pytest.approx(0)
    assert metrics["period_AAA_contribution_pct"] == pytest.approx(0)


def test_modified_dietz_weights_midperiod_deposit():
    result = modified_dietz_return(
        1000,
        1600,
        date(2026, 1, 1),
        date(2026, 1, 31),
        [(date(2026, 1, 16), 500)],
    )
    assert result == pytest.approx(100 / 1250)


def test_quantity_change_requires_matching_transaction():
    assert (
        quantity_change_warnings(
            {"AAA": 10},
            {"AAA": 15},
            [Transaction(date=date(2026, 1, 2), type="buy", symbol="AAA", quantity=5)],
        )
        == []
    )
    warning = quantity_change_warnings({"AAA": 10}, {"AAA": 15}, [])
    assert "işlem kayıtlarının neti" in warning[0]
