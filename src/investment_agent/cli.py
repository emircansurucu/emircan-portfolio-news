from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import httpx

from investment_agent.config import Settings, load_portfolio
from investment_agent.models import Cadence
from investment_agent.providers.fixtures import (
    FixtureEventsProvider,
    FixtureFxProvider,
    FixtureMacroProvider,
    FixtureMarketProvider,
    FixtureMetalsProvider,
)
from investment_agent.providers.fred import FredMacroDataProvider
from investment_agent.providers.llm import OpenAILLMProvider
from investment_agent.providers.news import OfficialFeedNewsProvider
from investment_agent.providers.sec import EdgarToolsSecFilingsProvider
from investment_agent.providers.telegram import TelegramDeliveryProvider
from investment_agent.providers.yahoo import (
    YahooFxProvider,
    YahooMarketDataProvider,
    YahooPreciousMetalsProvider,
)
from investment_agent.reporting import ReportRenderer
from investment_agent.service import InvestmentAgent
from investment_agent.state import JsonStateRepository


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Kişisel yatırım izleme ve araştırma raporu")
    result.add_argument("cadence", choices=[item.value for item in Cadence])
    result.add_argument(
        "--dry-run", action="store_true", help="Fixture kullan; state/Telegram yazma"
    )
    return result


async def async_main(cadence: Cadence, dry_run: bool) -> int:
    settings = Settings()
    portfolio_path = settings.portfolio_path
    if not portfolio_path.exists() and dry_run:
        portfolio_path = Path("portfolio.example.yaml")
    portfolio = load_portfolio(portfolio_path)
    common = {
        "portfolio": portfolio,
        "state": JsonStateRepository(settings.data_dir),
        "renderer": ReportRenderer(settings.reports_dir),
        "timezone": settings.timezone,
        "report_base_url": settings.report_base_url,
        "max_llm_events_per_run": settings.max_llm_events_per_run,
        "llm_max_concurrency": settings.llm_max_concurrency,
        "llm_retry_attempts": settings.llm_retry_attempts,
        "llm_retry_backoff_seconds": settings.llm_retry_backoff_seconds,
    }
    if dry_run:
        # Do not construct httpx/OpenAI/live adapters: proxy variables cannot affect fixture mode.
        events = FixtureEventsProvider()
        agent = InvestmentAgent(
            **common,
            market=FixtureMarketProvider(),
            metals=FixtureMetalsProvider(),
            fx=FixtureFxProvider(),
            sec=events,
            news=events,
            macro=FixtureMacroProvider(),
        )
        result = await agent.run(cadence, dry_run=True)
    else:
        timeout = httpx.Timeout(settings.http_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            market = YahooMarketDataProvider(client)
            metals = YahooPreciousMetalsProvider(market)
            fx = YahooFxProvider(market)
            sec = EdgarToolsSecFilingsProvider(settings.sec_identity)
            news = OfficialFeedNewsProvider(client, settings.ir_feeds)
            macro = FredMacroDataProvider(
                client,
                settings.fred_api_key.get_secret_value() if settings.fred_api_key else None,
            )
            llm = None
            if (
                settings.llm_provider == "openai"
                and settings.openai_api_key
                and settings.openai_model
            ):
                llm = OpenAILLMProvider(
                    settings.openai_api_key.get_secret_value(), settings.openai_model
                )
            delivery = None
            if settings.telegram_bot_token and settings.telegram_chat_id:
                delivery = TelegramDeliveryProvider(
                    client,
                    settings.telegram_bot_token.get_secret_value(),
                    settings.telegram_chat_id,
                )
            agent = InvestmentAgent(
                **common,
                market=market,
                metals=metals,
                fx=fx,
                sec=sec,
                news=news,
                macro=macro,
                llm=llm,
                delivery=delivery,
            )
            result = await agent.run(cadence)
    print(f"Markdown: {result.markdown_path}")
    print(f"HTML: {result.html_path}")
    if result.provider_failures:
        print("Başarısız sağlayıcılar: " + ", ".join(result.provider_failures))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = parser().parse_args()
    return asyncio.run(async_main(Cadence(arguments.cadence), arguments.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
