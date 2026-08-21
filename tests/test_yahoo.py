from __future__ import annotations

import httpx
import pytest

from investment_agent.providers.yahoo import YahooMarketDataProvider


@pytest.mark.asyncio
async def test_yahoo_http_is_mocked():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "MSFT" in str(request.url)
        return httpx.Response(
            200,
            json={
                "chart": {
                    "result": [
                        {
                            "meta": {
                                "regularMarketPrice": 420,
                                "regularMarketPreviousClose": 410,
                                "regularMarketTime": 1_775_000_000,
                            },
                            "timestamp": [1_775_000_000],
                            "indicators": {"quote": [{"close": [420]}]},
                        }
                    ]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        quote = (await YahooMarketDataProvider(client).get_quotes(["MSFT"])).data["MSFT"]
    assert quote.price_usd == 420
    assert quote.daily_return == pytest.approx(420 / 410 - 1)
