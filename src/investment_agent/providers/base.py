from __future__ import annotations

from datetime import datetime
from typing import Protocol

from investment_agent.models import AIEventAnalysis, MacroObservation, MaterialEvent, PriceQuote


class ProviderResult[T]:
    def __init__(
        self,
        data: T,
        *,
        warnings: list[str] | None = None,
        successful_scopes: list[str] | None = None,
    ) -> None:
        self.data = data
        self.warnings = warnings or []
        self.successful_scopes = successful_scopes or []


class NonRetryableLLMError(RuntimeError):
    """A validated provider response failed permanently and should not be retried again."""


class MarketDataProvider(Protocol):
    name: str

    async def get_quotes(self, symbols: list[str]) -> ProviderResult[dict[str, PriceQuote]]: ...


class SecFilingsProvider(Protocol):
    name: str

    async def get_filings(
        self, symbols: list[str], since: datetime | None
    ) -> ProviderResult[list[MaterialEvent]]: ...


class CompanyNewsProvider(Protocol):
    name: str

    async def get_announcements(
        self, symbols: list[str], since: datetime | None
    ) -> ProviderResult[list[MaterialEvent]]: ...


class MacroDataProvider(Protocol):
    name: str

    async def get_observations(self) -> ProviderResult[list[MacroObservation]]: ...


class PreciousMetalsProvider(Protocol):
    name: str

    async def get_metals(self) -> ProviderResult[dict[str, PriceQuote]]: ...


class FxProvider(Protocol):
    name: str

    async def get_usdtry(self) -> PriceQuote: ...


class LLMProvider(Protocol):
    name: str

    async def analyze(self, event: MaterialEvent) -> AIEventAnalysis: ...


class ReportDeliveryProvider(Protocol):
    name: str

    async def deliver(self, text: str) -> None: ...
