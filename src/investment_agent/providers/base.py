from __future__ import annotations

from datetime import datetime
from typing import Protocol

from investment_agent.models import AIEventAnalysis, MacroObservation, MaterialEvent, PriceQuote


class MarketDataProvider(Protocol):
    name: str

    async def get_quotes(self, symbols: list[str]) -> dict[str, PriceQuote]: ...


class SecFilingsProvider(Protocol):
    name: str

    async def get_filings(
        self, symbols: list[str], since: datetime | None
    ) -> list[MaterialEvent]: ...


class CompanyNewsProvider(Protocol):
    name: str

    async def get_announcements(
        self, symbols: list[str], since: datetime | None
    ) -> list[MaterialEvent]: ...


class MacroDataProvider(Protocol):
    name: str

    async def get_observations(self) -> list[MacroObservation]: ...


class PreciousMetalsProvider(Protocol):
    name: str

    async def get_metals(self) -> dict[str, PriceQuote]: ...


class FxProvider(Protocol):
    name: str

    async def get_usdtry(self) -> PriceQuote: ...


class LLMProvider(Protocol):
    name: str

    async def analyze(self, event: MaterialEvent) -> AIEventAnalysis: ...


class ReportDeliveryProvider(Protocol):
    name: str

    async def deliver(self, text: str) -> None: ...
