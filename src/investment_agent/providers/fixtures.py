from __future__ import annotations

from datetime import UTC, datetime

from investment_agent.models import MacroObservation, MaterialEvent, PriceQuote, SourceRecord
from investment_agent.providers.base import ProviderResult

FIXTURE_PRICES = {
    "MSFT": (428.50, 425.20),
    "RKLB": (28.40, 27.90),
    "ASTS": (51.20, 52.10),
    "VOO": (595.40, 592.90),
    "QQQM": (244.10, 242.70),
    "GOLD": (108.20, 107.50),
    "SILVER": (1.19, 1.17),
    "SP500": (6510.20, 6482.10),
    "NASDAQ100": (23810.40, 23690.20),
    "DXY": (98.30, 98.15),
    "USDTRY": (40.82, 40.77),
}


def fixture_quote(symbol: str, now: datetime | None = None) -> PriceQuote:
    at = now or datetime.now(UTC)
    price, previous = FIXTURE_PRICES[symbol]
    return PriceQuote(
        symbol=symbol,
        price_usd=price,
        previous_close_usd=previous,
        as_of=at,
        delayed=True,
        source=SourceRecord(
            title=f"Çevrimdışı test verisi: {symbol}",
            url=f"https://example.invalid/fixtures/{symbol}",
            published_at=at,
            retrieved_at=at,
            provider="Çevrimdışı fixture",
            is_primary=False,
        ),
    )


class FixtureMarketProvider:
    name = "Çevrimdışı fixture"

    async def get_quotes(self, symbols: list[str]) -> ProviderResult[dict[str, PriceQuote]]:
        return ProviderResult(
            {symbol: fixture_quote(symbol) for symbol in symbols},
            successful_scopes=[f"fixture:{symbol}" for symbol in symbols],
        )


class FixtureFxProvider:
    name = "Çevrimdışı fixture"

    async def get_usdtry(self) -> PriceQuote:
        return fixture_quote("USDTRY")


class FixtureMetalsProvider:
    name = "Çevrimdışı fixture"

    async def get_metals(self) -> ProviderResult[dict[str, PriceQuote]]:
        return ProviderResult(
            {symbol: fixture_quote(symbol) for symbol in ("GOLD", "SILVER")},
            successful_scopes=["fixture:GOLD", "fixture:SILVER"],
        )


class FixtureMacroProvider:
    name = "Çevrimdışı fixture"

    async def get_observations(self) -> ProviderResult[list[MacroObservation]]:
        now = datetime.now(UTC)
        return ProviderResult(
            [
                MacroObservation(
                    series_id="DGS10",
                    name_tr="ABD 10 yıllık Hazine tahvili getirisi",
                    value=4.21,
                    unit="%",
                    observed_at=now,
                    source=SourceRecord(
                        title="Çevrimdışı test verisi: DGS10",
                        url="https://example.invalid/fixtures/DGS10",
                        published_at=now,
                        retrieved_at=now,
                        provider=self.name,
                        is_primary=False,
                    ),
                )
            ],
            successful_scopes=["fixture:DGS10"],
        )


class FixtureEventsProvider:
    name = "Çevrimdışı fixture"

    async def get_filings(
        self, symbols: list[str], since: datetime | None
    ) -> ProviderResult[list[MaterialEvent]]:
        return ProviderResult([], successful_scopes=[f"fixture:sec:{symbol}" for symbol in symbols])

    async def get_announcements(
        self, symbols: list[str], since: datetime | None
    ) -> ProviderResult[list[MaterialEvent]]:
        return ProviderResult([], successful_scopes=["fixture:ir"])
