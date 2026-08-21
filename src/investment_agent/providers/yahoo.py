from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx

from investment_agent.models import PriceQuote, SourceRecord
from investment_agent.providers.base import ProviderResult

YAHOO_SYMBOLS = {
    "MSFT": "MSFT",
    "RKLB": "RKLB",
    "ASTS": "ASTS",
    "VOO": "VOO",
    "QQQM": "QQQM",
    "SP500": "^GSPC",
    "NASDAQ100": "^NDX",
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "USDTRY": "TRY=X",
    "DXY": "DX-Y.NYB",
}
TROY_OUNCE_GRAMS = 31.1034768


class YahooMarketDataProvider:
    """Unofficial/delayed MVP adapter; replace with a licensed feed in production."""

    name = "Yahoo Finance (gecikmeli/resmî olmayan)"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def _quote(self, symbol: str) -> PriceQuote:
        remote_symbol = YAHOO_SYMBOLS.get(symbol, symbol)
        response = await self.client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{remote_symbol}",
            params={"interval": "1d", "range": "5d"},
        )
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]
        meta = result["meta"]
        closes = [value for value in result["indicators"]["quote"][0]["close"] if value is not None]
        price = float(meta.get("regularMarketPrice") or closes[-1])
        previous = (
            meta.get("regularMarketPreviousClose")
            or meta.get("previousClose")
            or (closes[-2] if len(closes) >= 2 else None)
        )
        timestamp = datetime.fromtimestamp(
            int(meta.get("regularMarketTime") or result["timestamp"][-1]), tz=UTC
        )
        if symbol in {"GOLD", "SILVER"}:
            price /= TROY_OUNCE_GRAMS
            previous = float(previous) / TROY_OUNCE_GRAMS if previous else None
        return PriceQuote(
            symbol=symbol,
            price_usd=price,
            previous_close_usd=float(previous) if previous else None,
            as_of=timestamp,
            delayed=True,
            source=SourceRecord(
                title=f"{remote_symbol} piyasa verisi",
                url=f"https://finance.yahoo.com/quote/{remote_symbol}",
                published_at=timestamp,
                retrieved_at=datetime.now(UTC),
                provider=self.name,
                is_primary=False,
            ),
        )

    async def get_quotes(self, symbols: list[str]) -> ProviderResult[dict[str, PriceQuote]]:
        results = await asyncio.gather(
            *(self._quote(symbol) for symbol in symbols), return_exceptions=True
        )
        quotes: dict[str, PriceQuote] = {}
        warnings: list[str] = []
        successful_scopes: list[str] = []
        for symbol, result in zip(symbols, results, strict=True):
            if isinstance(result, BaseException):
                warnings.append(f"{symbol}: {type(result).__name__} nedeniyle fiyat alınamadı")
            else:
                quotes[symbol] = result
                successful_scopes.append(f"yahoo:{symbol}")
        return ProviderResult(quotes, warnings=warnings, successful_scopes=successful_scopes)


class YahooPreciousMetalsProvider:
    name = YahooMarketDataProvider.name

    def __init__(self, market: YahooMarketDataProvider) -> None:
        self.market = market

    async def get_metals(self) -> ProviderResult[dict[str, PriceQuote]]:
        return await self.market.get_quotes(["GOLD", "SILVER"])


class YahooFxProvider:
    name = YahooMarketDataProvider.name

    def __init__(self, market: YahooMarketDataProvider) -> None:
        self.market = market

    async def get_usdtry(self) -> PriceQuote:
        result = await self.market.get_quotes(["USDTRY"])
        if "USDTRY" not in result.data:
            raise RuntimeError(result.warnings[0] if result.warnings else "USD/TRY alınamadı")
        return result.data["USDTRY"]
