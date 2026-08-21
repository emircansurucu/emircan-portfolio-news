from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime

import numpy as np
import pandas as pd
import quantstats as qs

from investment_agent.models import (
    PortfolioConfig,
    PortfolioSnapshot,
    PositionValuation,
    PriceQuote,
    Transaction,
    TransactionType,
)


def convert_usd_to_try(amount_usd: float, usdtry: float) -> float:
    if usdtry <= 0:
        raise ValueError("USD/TRY must be positive")
    return amount_usd * usdtry


def weighted_contribution(
    quantity: float, current_price: float, previous_price: float, previous_total: float
) -> float:
    if previous_total <= 0:
        raise ValueError("Previous portfolio value must be positive")
    return quantity * (current_price - previous_price) / previous_total


def value_portfolio(
    portfolio: PortfolioConfig,
    quotes: dict[str, PriceQuote],
    usdtry: float,
    as_of: datetime,
) -> PortfolioSnapshot:
    missing = [position.symbol for position in portfolio.positions if position.symbol not in quotes]
    if missing:
        raise ValueError(f"Missing quotes for: {', '.join(missing)}")

    raw_values = {
        position.symbol: position.quantity * quotes[position.symbol].price_usd
        for position in portfolio.positions
    }
    total = sum(raw_values.values())
    previous_total = sum(
        position.quantity
        * (quotes[position.symbol].previous_close_usd or quotes[position.symbol].price_usd)
        for position in portfolio.positions
    )
    valuations: list[PositionValuation] = []
    for position in portfolio.positions:
        quote = quotes[position.symbol]
        value_usd = raw_values[position.symbol]
        cost_basis = (
            position.quantity * position.average_cost_usd
            if position.average_cost_usd is not None
            else None
        )
        contribution = None
        if quote.previous_close_usd is not None and previous_total > 0:
            contribution = weighted_contribution(
                position.quantity, quote.price_usd, quote.previous_close_usd, previous_total
            )
        valuations.append(
            PositionValuation(
                symbol=position.symbol,
                asset_type=position.asset_type,
                quantity=position.quantity,
                price_usd=quote.price_usd,
                value_usd=value_usd,
                value_try=convert_usd_to_try(value_usd, usdtry),
                weight=value_usd / total if total else 0,
                cost_basis_usd=cost_basis,
                unrealized_pnl_usd=value_usd - cost_basis if cost_basis is not None else None,
                daily_contribution_pct=contribution * 100 if contribution is not None else None,
            )
        )
    daily_return = (total / previous_total - 1) * 100 if previous_total > 0 else None
    return PortfolioSnapshot(
        as_of=as_of,
        usdtry=usdtry,
        total_value_usd=total,
        total_value_try=convert_usd_to_try(total, usdtry),
        daily_return_pct=daily_return,
        positions=valuations,
    )


def external_cash_flows(transactions: Iterable[Transaction]) -> float:
    """Return net cash introduced by the owner, excluding trading and dividends."""
    total = 0.0
    for transaction in transactions:
        amount = transaction.amount_usd or 0.0
        if transaction.type is TransactionType.DEPOSIT:
            total += amount
        elif transaction.type is TransactionType.WITHDRAWAL:
            total -= amount
    return total


def investment_return(
    begin_value: float, end_value: float, net_external_flow: float
) -> float | None:
    if begin_value <= 0:
        return None
    return (end_value - begin_value - net_external_flow) / begin_value


def maximum_drawdown(values: Sequence[float]) -> float | None:
    if not values:
        return None
    series = np.asarray(values, dtype=float)
    if np.any(series <= 0):
        raise ValueError("Portfolio values must be positive")
    peaks = np.maximum.accumulate(series)
    return float(np.min(series / peaks - 1.0))


def annualized_volatility(values: Sequence[float], periods: int = 252) -> float | None:
    if len(values) < 3:
        return None
    returns = pd.Series(values, dtype=float).pct_change().dropna()
    return float(qs.stats.volatility(returns, periods=periods, annualize=True))


def xirr(cash_flows: Sequence[tuple[date, float]], guess: float = 0.1) -> float | None:
    if (
        len(cash_flows) < 2
        or len({when for when, _ in cash_flows}) < 2
        or not any(v < 0 for _, v in cash_flows)
        or not any(v > 0 for _, v in cash_flows)
    ):
        return None
    ordered = sorted(cash_flows)
    origin = ordered[0][0]

    def npv(rate: float) -> float:
        return sum(value / (1 + rate) ** ((when - origin).days / 365.0) for when, value in ordered)

    low, high = -0.9999, max(guess, 1.0)
    low_value, high_value = npv(low), npv(high)
    while low_value * high_value > 0 and high < 1_000_000:
        high *= 2
        high_value = npv(high)
    if low_value * high_value > 0:
        return None
    for _ in range(200):
        middle = (low + high) / 2
        value = npv(middle)
        if abs(value) < 1e-9:
            return middle
        if low_value * value <= 0:
            high = middle
        else:
            low, low_value = middle, value
    return (low + high) / 2


def history_metrics(
    records: Sequence[dict[str, object]], cadence: str, transactions: Sequence[Transaction]
) -> dict[str, float | None]:
    if not records:
        return {}
    frame = pd.DataFrame(records)
    frame["as_of"] = pd.to_datetime(frame["as_of"], utc=True)
    frame = frame.sort_values("as_of")
    values = frame["total_value_usd"].astype(float).tolist()
    begin, end = values[0], values[-1]
    flow = external_cash_flows(transactions)
    deposits = sum(
        transaction.amount_usd or 0.0
        for transaction in transactions
        if transaction.type is TransactionType.DEPOSIT
    )
    withdrawals = sum(
        transaction.amount_usd or 0.0
        for transaction in transactions
        if transaction.type is TransactionType.WITHDRAWAL
    )
    metrics: dict[str, float | None] = {
        "begin_value_usd": begin,
        "end_value_usd": end,
        "net_external_flow_usd": flow,
        "deposits_usd": deposits,
        "withdrawals_usd": withdrawals,
        "dividends_usd": sum(
            transaction.amount_usd or 0.0
            for transaction in transactions
            if transaction.type is TransactionType.DIVIDEND
        ),
        "commissions_usd": sum(
            transaction.commission_usd
            + (
                transaction.amount_usd or 0.0
                if transaction.type is TransactionType.COMMISSION
                else 0.0
            )
            for transaction in transactions
        ),
        "investment_return_pct": (
            investment_return(begin, end, flow) * 100
            if len(records) >= 2 and investment_return(begin, end, flow) is not None
            else None
        ),
        "maximum_drawdown_pct": (
            maximum_drawdown(values) * 100
            if len(records) >= 2 and maximum_drawdown(values) is not None
            else None
        ),
        "annualized_volatility_pct": (
            annualized_volatility(values) * 100
            if annualized_volatility(values) is not None
            else None
        ),
    }
    if "total_value_try" in frame and len(frame["total_value_try"].dropna()) >= 2:
        try_values = frame["total_value_try"].dropna().astype(float)
        metrics["try_value_change_pct"] = (try_values.iloc[-1] / try_values.iloc[0] - 1) * 100

    first_prices = records[0].get("market_prices_usd", {})
    last_prices = records[-1].get("market_prices_usd", {})
    if len(records) >= 2 and isinstance(first_prices, dict) and isinstance(last_prices, dict):
        for symbol in set(first_prices) & set(last_prices):
            start_price = float(first_prices[symbol])
            if start_price > 0:
                metrics[f"period_{symbol}_return_pct"] = (
                    float(last_prices[symbol]) / start_price - 1
                ) * 100
    first_values = records[0].get("position_values_usd", {})
    last_values = records[-1].get("position_values_usd", {})
    traded_symbols = {
        transaction.symbol
        for transaction in transactions
        if transaction.type in {TransactionType.BUY, TransactionType.SELL}
    }
    if len(records) >= 2 and isinstance(first_values, dict) and isinstance(last_values, dict):
        for symbol in set(first_values) & set(last_values):
            if symbol not in traded_symbols and begin > 0:
                metrics[f"period_{symbol}_contribution_pct"] = (
                    (float(last_values[symbol]) - float(first_values[symbol])) / begin * 100
                )
    if cadence == "monthly":
        origin = frame.iloc[0]["as_of"].date()
        end_date = frame.iloc[-1]["as_of"].date()
        dated_flows = [(origin, -begin)]
        dated_flows.extend(
            (
                transaction.date,
                -(transaction.amount_usd or 0.0)
                if transaction.type is TransactionType.DEPOSIT
                else transaction.amount_usd or 0.0,
            )
            for transaction in transactions
            if transaction.type in {TransactionType.DEPOSIT, TransactionType.WITHDRAWAL}
            and transaction.amount_usd
        )
        dated_flows.append((end_date, end))
        result = xirr(dated_flows)
        metrics["xirr_pct"] = result * 100 if result is not None else None
    return metrics
