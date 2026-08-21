from __future__ import annotations

from datetime import date

import httpx
import pytest

from investment_agent.providers.fred import FredMacroDataProvider
from investment_agent.providers.news import OfficialFeedNewsProvider
from investment_agent.providers.sec import EdgarToolsSecFilingsProvider
from investment_agent.providers.yahoo import YahooMarketDataProvider


def yahoo_payload(price: float = 420) -> dict:
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": price,
                        "regularMarketPreviousClose": price - 1,
                        "regularMarketTime": 1_775_000_000,
                    },
                    "timestamp": [1_775_000_000],
                    "indicators": {"quote": [{"close": [price]}]},
                }
            ]
        }
    }


@pytest.mark.asyncio
async def test_yahoo_keeps_successful_symbols_when_one_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        if "BAD" in str(request.url):
            return httpx.Response(503)
        return httpx.Response(200, json=yahoo_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await YahooMarketDataProvider(client).get_quotes(["MSFT", "BAD"])
    assert set(result.data) == {"MSFT"}
    assert result.successful_scopes == ["yahoo:MSFT"]
    assert "BAD" in result.warnings[0]
    assert "query1.finance" not in result.warnings[0]


@pytest.mark.asyncio
async def test_official_feeds_keep_successful_feed_and_report_missing_configuration():
    xml = b"""<rss><channel><item><title>Resmi duyuru</title>
    <link>https://example.com/official</link><pubDate>Thu, 20 Aug 2026 20:00:00 GMT</pubDate>
    <description><![CDATA[<b>kisa</b> ozet]]></description></item></channel></rss>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(500) if "bad" in str(request.url) else httpx.Response(200, content=xml)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OfficialFeedNewsProvider(
            client,
            {"AAA": ["https://example.com/good.xml", "https://example.com/bad.xml"]},
        )
        result = await provider.get_announcements(["AAA"], None)
        empty = await OfficialFeedNewsProvider(client, {}).get_announcements(["AAA"], None)
    assert len(result.data) == 1
    assert result.data[0].summary == "kisa ozet"
    assert len(result.successful_scopes) == 1
    assert len(result.warnings) == 1
    assert empty.data == []
    assert "yapılandırılmadı" in empty.warnings[0]


@pytest.mark.asyncio
async def test_fred_isolates_series_failure_and_hides_api_key():
    def handler(request: httpx.Request) -> httpx.Response:
        series = request.url.params["series_id"]
        if series == "DGS10":
            return httpx.Response(429)
        return httpx.Response(200, json={"observations": [{"date": "2026-08-01", "value": "4.2"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await FredMacroDataProvider(client, "super-secret").get_observations()
    assert len(result.data) == 3
    assert "DGS10" in result.warnings[0]
    assert "super-secret" not in result.warnings[0]
    assert len(result.successful_scopes) == 3


@pytest.mark.asyncio
async def test_sec_isolates_company_failure(monkeypatch):
    import edgar

    class Filing:
        filing_date = date(2026, 8, 20)
        accession_no = "0001"
        accession_number = "0001"
        form = "8-K"
        filing_url = "https://www.sec.gov/filing"
        homepage_url = ""

    class Company:
        def __init__(self, symbol):
            if symbol == "BAD":
                raise RuntimeError("company unavailable")

        def get_filings(self, **kwargs):
            return [Filing()]

    monkeypatch.setattr(edgar, "Company", Company)
    monkeypatch.setattr(edgar, "set_identity", lambda identity: None)
    result = await EdgarToolsSecFilingsProvider("Name test@example.com").get_filings(
        ["GOOD", "BAD"], None
    )
    assert len(result.data) == 1
    assert result.data[0].title.endswith("(yalnız filing tespiti)")
    assert result.successful_scopes == ["sec:GOOD"]
    assert "BAD" in result.warnings[0]
