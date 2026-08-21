from __future__ import annotations

from datetime import UTC, datetime

import pytest

from investment_agent.models import PortfolioConfig, PriceQuote, SourceRecord


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 20, 20, 0, tzinfo=UTC)


@pytest.fixture
def portfolio() -> PortfolioConfig:
    return PortfolioConfig.model_validate(
        {
            "base_currency": "TRY",
            "positions": [
                {
                    "symbol": "AAA",
                    "asset_type": "stock",
                    "quantity": 2,
                    "average_cost_usd": 80,
                },
                {"symbol": "GOLD", "asset_type": "precious_metal", "quantity": 10, "unit": "gram"},
            ],
            "transactions": [],
            "benchmark_symbols": [],
        }
    )


def make_quote(symbol: str, price: float, previous: float, now: datetime) -> PriceQuote:
    return PriceQuote(
        symbol=symbol,
        price_usd=price,
        previous_close_usd=previous,
        as_of=now,
        delayed=False,
        source=SourceRecord(
            title=f"{symbol} source",
            url=f"https://example.com/{symbol}",
            published_at=now,
            retrieved_at=now,
            provider="test",
            is_primary=True,
        ),
    )
