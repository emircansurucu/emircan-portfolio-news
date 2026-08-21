from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from investment_agent.calculations import history_metrics, value_portfolio
from investment_agent.models import (
    Cadence,
    PortfolioConfig,
    ProviderStatus,
    ReportContext,
    RunResult,
)
from investment_agent.providers.base import (
    CompanyNewsProvider,
    FxProvider,
    LLMProvider,
    MacroDataProvider,
    MarketDataProvider,
    PreciousMetalsProvider,
    ReportDeliveryProvider,
    SecFilingsProvider,
)
from investment_agent.reporting import ReportRenderer, telegram_summary
from investment_agent.state import StateRepository

LOGGER = logging.getLogger(__name__)


def safe_provider_error(error: BaseException) -> str:
    if isinstance(error, RuntimeError):
        return str(error)[:300]
    return f"{type(error).__name__}: sağlayıcı isteği başarısız"


def is_us_market_session(at: datetime) -> bool:
    import exchange_calendars as xcals

    calendar = xcals.get_calendar("XNYS")
    market_date = at.astimezone(ZoneInfo("America/New_York")).date()
    return bool(calendar.is_session(pd.Timestamp(market_date)))


class InvestmentAgent:
    def __init__(
        self,
        *,
        portfolio: PortfolioConfig,
        state: StateRepository,
        renderer: ReportRenderer,
        market: MarketDataProvider,
        metals: PreciousMetalsProvider,
        fx: FxProvider,
        sec: SecFilingsProvider,
        news: CompanyNewsProvider,
        macro: MacroDataProvider,
        llm: LLMProvider | None = None,
        delivery: ReportDeliveryProvider | None = None,
        timezone: str = "Europe/Istanbul",
        report_base_url: str | None = None,
    ) -> None:
        self.portfolio = portfolio
        self.state = state
        self.renderer = renderer
        self.market = market
        self.metals = metals
        self.fx = fx
        self.sec = sec
        self.news = news
        self.macro = macro
        self.llm = llm
        self.delivery = delivery
        self.timezone = ZoneInfo(timezone)
        self.report_base_url = report_base_url

    async def run(
        self, cadence: Cadence, *, dry_run: bool = False, now: datetime | None = None
    ) -> RunResult:
        at_utc = (now or datetime.now(UTC)).astimezone(UTC)
        at_local = at_utc.astimezone(self.timezone)
        since = self.state.last_success(cadence)
        # Overlap protects against a temporarily failed event provider; fingerprints remove repeats.
        fetch_since = since - timedelta(days=7) if since else at_utc - timedelta(days=90)
        tracked_symbols = [position.symbol for position in self.portfolio.positions]
        company_symbols = [
            position.symbol
            for position in self.portfolio.positions
            if position.asset_type.value == "stock"
        ]
        statuses: list[ProviderStatus] = []
        warnings: list[str] = []
        skip_market = cadence is Cadence.DAILY and not dry_run and not is_us_market_session(at_utc)

        event_results = await asyncio.gather(
            self.sec.get_filings(company_symbols, fetch_since),
            self.news.get_announcements(tracked_symbols, fetch_since),
            self.macro.get_observations(),
            return_exceptions=True,
        )
        events = []
        for label, result in zip(
            (self.sec.name, self.news.name, self.macro.name), event_results, strict=True
        ):
            if isinstance(result, BaseException):
                warning = safe_provider_error(result)
                statuses.append(ProviderStatus(provider=label, success=False, warning=warning))
                LOGGER.warning("Sağlayıcı başarısız: %s: %s", label, warning)
            else:
                statuses.append(ProviderStatus(provider=label, success=True))
                if label == self.macro.name:
                    macro_observations = result
                else:
                    events.extend(result)
        macro_observations = locals().get("macro_observations", [])
        events = sorted(
            (event for event in events if not self.state.is_processed(event, cadence)),
            key=lambda event: event.occurred_at,
            reverse=True,
        )

        snapshot = None
        quotes = {}
        if skip_market:
            warnings.append(
                "ABD piyasası kapalı: normal günlük piyasa raporlaması atlandı; olay taraması sürdü."
            )
            statuses.append(
                ProviderStatus(
                    provider="NYSE işlem takvimi", success=True, warning="Piyasa tatili/hafta sonu"
                )
            )
        else:
            securities = [
                position.symbol
                for position in self.portfolio.positions
                if position.asset_type.value in {"stock", "etf"}
            ] + self.portfolio.benchmark_symbols
            market_results = await asyncio.gather(
                self.market.get_quotes(securities),
                self.metals.get_metals(),
                self.fx.get_usdtry(),
                return_exceptions=True,
            )
            labels = ("Piyasa fiyatları", "Değerli metaller", "USD/TRY")
            for label, provider, result in zip(
                labels, (self.market, self.metals, self.fx), market_results, strict=True
            ):
                if isinstance(result, BaseException):
                    warning = safe_provider_error(result)
                    statuses.append(
                        ProviderStatus(
                            provider=f"{label} — {provider.name}", success=False, warning=warning
                        )
                    )
                    LOGGER.warning("Sağlayıcı başarısız: %s: %s", label, warning)
                else:
                    statuses.append(
                        ProviderStatus(provider=f"{label} — {provider.name}", success=True)
                    )
                    if label == "USD/TRY":
                        fx_quote = result
                    else:
                        quotes.update(result)
            fx_quote = locals().get("fx_quote")
            position_symbols = {position.symbol for position in self.portfolio.positions}
            if fx_quote is not None and position_symbols.issubset(quotes):
                snapshot = value_portfolio(
                    self.portfolio,
                    {symbol: quotes[symbol] for symbol in position_symbols},
                    fx_quote.price_usd,
                    at_local,
                )
                quotes["USDTRY"] = fx_quote
                snapshot.market_prices_usd = {
                    symbol: quote.price_usd for symbol, quote in quotes.items()
                }
            else:
                missing = sorted(position_symbols - set(quotes))
                warnings.append(
                    "Portföy değerlemesi eksik veri nedeniyle yapılamadı"
                    + (f": {', '.join(missing)}" if missing else ": USD/TRY")
                )
            if any(quote.delayed for quote in quotes.values()):
                warnings.append(
                    "Piyasa fiyatları gecikmeli ve resmî olmayan bir MVP kaynağından gelebilir."
                )
            stale = [
                quote.symbol
                for quote in quotes.values()
                if at_utc - quote.as_of.astimezone(UTC) > timedelta(hours=36)
            ]
            if stale:
                warnings.append("36 saatten eski piyasa verileri: " + ", ".join(sorted(stale)))

        analyses = []
        ai_reason = None
        if not self.llm:
            ai_reason = "LLM API anahtarı ve model adı yapılandırılmamış."
            statuses.append(ProviderStatus(provider="AI yorumu", success=False, warning=ai_reason))
        elif not events:
            statuses.append(ProviderStatus(provider=self.llm.name, success=True))
        else:
            llm_results = await asyncio.gather(
                *(self.llm.analyze(event) for event in events), return_exceptions=True
            )
            failures = 0
            for result in llm_results:
                if isinstance(result, BaseException):
                    failures += 1
                    LOGGER.warning("AI yorumu atlandı: %s", safe_provider_error(result))
                else:
                    analyses.append(result)
            if failures:
                ai_reason = f"{failures} olay için şema/kaynak doğrulaması başarısız."
            statuses.append(
                ProviderStatus(provider=self.llm.name, success=failures == 0, warning=ai_reason)
            )

        history = self.state.history()
        if snapshot:
            history = [
                *history,
                {
                    "as_of": snapshot.as_of.isoformat(),
                    "total_value_usd": snapshot.total_value_usd,
                    "total_value_try": snapshot.total_value_try,
                    "market_prices_usd": snapshot.market_prices_usd,
                    "position_values_usd": {
                        position.symbol: position.value_usd for position in snapshot.positions
                    },
                },
            ]
        period_days = 7 if cadence is Cadence.WEEKLY else 31
        if cadence is not Cadence.DAILY:
            cutoff = at_utc - timedelta(days=period_days)
            history = [
                record
                for record in history
                if pd.Timestamp(record["as_of"]).to_pydatetime().astimezone(UTC) >= cutoff
            ]
        if cadence is Cadence.DAILY:
            period_transactions = []
        else:
            first_history_date = (
                pd.Timestamp(history[0]["as_of"]).date() if history else cutoff.date()
            )
            period_transactions = [
                transaction
                for transaction in self.portfolio.transactions
                if transaction.date >= max(cutoff.date(), first_history_date)
            ]
        metrics = history_metrics(history, cadence.value, period_transactions)
        if snapshot:
            for asset_type in ("stock", "etf", "precious_metal"):
                metrics[f"allocation_{asset_type}_pct"] = sum(
                    position.weight * 100
                    for position in snapshot.positions
                    if position.asset_type.value == asset_type
                )
        market_data_at = max((quote.as_of for quote in quotes.values()), default=None)
        report_id = f"{cadence.value}-{at_local.strftime('%Y%m%dT%H%M%S%z')}"
        context = ReportContext(
            cadence=cadence,
            as_of=at_local,
            since=since.astimezone(self.timezone) if since else None,
            market_data_at=market_data_at.astimezone(self.timezone) if market_data_at else None,
            snapshot=snapshot,
            events=events,
            analyses=analyses,
            macro=macro_observations,
            market_sources=[quote.source for quote in quotes.values()],
            market_quotes=list(quotes.values()),
            providers=statuses,
            freshness_warnings=warnings,
            ai_unavailable_reason=ai_reason,
            period_metrics=metrics,
            report_id=report_id,
        )
        markdown_path, html_path = self.renderer.render(context)

        checkpoint_updated = False
        if not dry_run:
            self.state.commit_success(cadence, at_utc, report_id, snapshot, events)
            checkpoint_updated = True

        telegram_delivered = False
        if self.delivery and not dry_run:
            reference = (
                f"{self.report_base_url.rstrip('/')}/{html_path.name}"
                if self.report_base_url
                else str(html_path)
            )
            try:
                await self.delivery.deliver(telegram_summary(context, reference))
                telegram_delivered = True
            except Exception as exc:  # Delivery must not invalidate a completed report.
                LOGGER.warning("Telegram gönderimi başarısız: %s", safe_provider_error(exc))

        return RunResult(
            cadence=cadence,
            report_id=report_id,
            markdown_path=str(markdown_path),
            html_path=str(html_path),
            checkpoint_updated=checkpoint_updated,
            telegram_delivered=telegram_delivered,
            skipped_market_reporting=skip_market,
            provider_failures=[status.provider for status in statuses if not status.success],
        )
