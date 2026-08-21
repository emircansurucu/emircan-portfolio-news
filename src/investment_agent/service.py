from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from investment_agent.calculations import (
    history_metrics,
    quantity_change_warnings,
    value_portfolio,
)
from investment_agent.models import (
    AIEventAnalysis,
    AnalysisDisposition,
    Cadence,
    MaterialEvent,
    PortfolioConfig,
    PriceQuote,
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
    NonRetryableLLMError,
    PreciousMetalsProvider,
    ProviderResult,
    ReportDeliveryProvider,
    SecFilingsProvider,
)
from investment_agent.reporting import ReportRenderer, telegram_summary
from investment_agent.state import StateRepository, event_fingerprint

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


def _market_session(quotes: dict[str, PriceQuote]) -> date | None:
    timestamps = [quote.as_of for quote in quotes.values()]
    if not timestamps:
        return None
    return max(timestamps).astimezone(ZoneInfo("America/New_York")).date()


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
        max_llm_events_per_run: int = 10,
        llm_max_concurrency: int = 3,
        llm_retry_attempts: int = 3,
        llm_retry_backoff_seconds: float = 0.5,
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
        self.max_llm_events_per_run = max_llm_events_per_run
        self.llm_max_concurrency = llm_max_concurrency
        self.llm_retry_attempts = llm_retry_attempts
        self.llm_retry_backoff_seconds = llm_retry_backoff_seconds

    def _fetch_since(self, scopes: list[str], at: datetime) -> datetime:
        checkpoints = [self.state.provider_checkpoint(scope) for scope in scopes]
        if not checkpoints or any(checkpoint is None for checkpoint in checkpoints):
            return at - timedelta(days=90)
        return min(checkpoint for checkpoint in checkpoints if checkpoint) - timedelta(days=7)

    async def _analyze_with_retry(
        self, event: MaterialEvent, semaphore: asyncio.Semaphore
    ) -> tuple[AIEventAnalysis | None, str | None]:
        assert self.llm is not None
        last_error: BaseException | None = None
        async with semaphore:
            for attempt in range(self.llm_retry_attempts):
                try:
                    return await self.llm.analyze(event), None
                except Exception as exc:
                    last_error = exc
                    if isinstance(exc, NonRetryableLLMError):
                        break
                    if attempt + 1 < self.llm_retry_attempts:
                        await asyncio.sleep(self.llm_retry_backoff_seconds * (2**attempt))
        assert last_error is not None
        return None, safe_provider_error(last_error)

    @staticmethod
    def _status_from_result(
        label: str,
        result: ProviderResult[object],
        statuses: list[ProviderStatus],
        warnings: list[str],
    ) -> None:
        warning = "; ".join(result.warnings) if result.warnings else None
        success = bool(result.successful_scopes) or not result.warnings
        statuses.append(ProviderStatus(provider=label, success=success, warning=warning))
        if warning:
            warnings.append(f"{label}: {warning}")

    async def run(
        self, cadence: Cadence, *, dry_run: bool = False, now: datetime | None = None
    ) -> RunResult:
        at_utc = (now or datetime.now(UTC)).astimezone(UTC)
        at_local = at_utc.astimezone(self.timezone)
        since = self.state.last_success(cadence)
        tracked_symbols = [position.symbol for position in self.portfolio.positions]
        company_symbols = [
            position.symbol
            for position in self.portfolio.positions
            if position.asset_type.value == "stock"
        ]
        sec_scopes = [f"sec:{symbol}" for symbol in company_symbols]
        news_scopes = list(getattr(self.news, "scopes", [])) or ["ir:configured-feeds"]
        statuses: list[ProviderStatus] = []
        warnings: list[str] = []
        successful_checkpoints: dict[str, datetime] = {}
        skip_market = cadence is Cadence.DAILY and not dry_run and not is_us_market_session(at_utc)

        event_results = await asyncio.gather(
            self.sec.get_filings(company_symbols, self._fetch_since(sec_scopes, at_utc)),
            self.news.get_announcements(tracked_symbols, self._fetch_since(news_scopes, at_utc)),
            self.macro.get_observations(),
            return_exceptions=True,
        )
        all_events: list[MaterialEvent] = []
        macro_observations = []
        for label, result in zip(
            (self.sec.name, self.news.name, self.macro.name), event_results, strict=True
        ):
            if isinstance(result, BaseException):
                warning = safe_provider_error(result)
                statuses.append(ProviderStatus(provider=label, success=False, warning=warning))
                warnings.append(f"{label}: {warning}")
                LOGGER.warning("Sağlayıcı başarısız: %s: %s", label, warning)
                continue
            self._status_from_result(label, result, statuses, warnings)
            successful_checkpoints.update({scope: at_utc for scope in result.successful_scopes})
            if label == self.macro.name:
                macro_observations = result.data
            else:
                all_events.extend(result.data)

        unique_events = {event_fingerprint(event): event for event in all_events}
        all_events = sorted(
            unique_events.values(), key=lambda event: event.occurred_at, reverse=True
        )
        if not dry_run:
            self.state.record_discovered(all_events, at_utc)
            persisted_report_events = self.state.unreported_events(
                cadence, since, at_utc - timedelta(days=90)
            )
            pending_analysis_events = self.state.pending_analysis_events()
            all_events = list(
                {
                    event_fingerprint(event): event
                    for event in [*all_events, *persisted_report_events, *pending_analysis_events]
                }.values()
            )
            report_events = sorted(
                persisted_report_events,
                key=lambda event: event.occurred_at,
                reverse=True,
            )
        else:
            report_events = [
                event for event in all_events if not self.state.is_processed(event, cadence)
            ]

        analyses: list[AIEventAnalysis] = []
        dispositions: list[AnalysisDisposition] = []
        analysis_by_event: dict[str, AIEventAnalysis] = {}
        newly_completed: dict[str, tuple[MaterialEvent, AIEventAnalysis]] = {}
        for event in all_events:
            saved = self.state.analysis_for(event)
            if saved is not None:
                analysis_by_event[event.event_id] = saved

        filings = [event for event in report_events if event.event_type == "filing"]
        for event in filings:
            reason = "Filing içeriği/olguları çıkarılmadı; yalnız resmî filing tespiti raporlandı."
            dispositions.append(
                AnalysisDisposition(event_id=event.event_id, status="skipped", reason=reason)
            )
            if not dry_run and self.state.analysis_state(event) != "completed":
                self.state.mark_analysis(event, "skipped", at_utc, reason=reason)

        candidates = [
            event
            for event in all_events
            if event.event_type != "filing" and event.event_id not in analysis_by_event
        ]
        selected = candidates[: self.max_llm_events_per_run]
        deferred = candidates[self.max_llm_events_per_run :]
        for event in deferred:
            reason = (
                f"MAX_LLM_EVENTS_PER_RUN={self.max_llm_events_per_run} sınırı nedeniyle ertelendi."
            )
            if event in report_events:
                dispositions.append(
                    AnalysisDisposition(event_id=event.event_id, status="deferred", reason=reason)
                )
            if not dry_run:
                self.state.mark_analysis(event, "deferred", at_utc, reason=reason)

        ai_reason = None
        if not self.llm:
            ai_reason = "LLM API anahtarı ve model adı yapılandırılmamış."
            statuses.append(ProviderStatus(provider="AI yorumu", success=False, warning=ai_reason))
            for event in report_events:
                if event.event_type != "filing" and event.event_id not in analysis_by_event:
                    dispositions.append(
                        AnalysisDisposition(
                            event_id=event.event_id, status="unavailable", reason=ai_reason
                        )
                    )
        elif selected:
            semaphore = asyncio.Semaphore(self.llm_max_concurrency)
            results = await asyncio.gather(
                *(self._analyze_with_retry(event, semaphore) for event in selected)
            )
            failed = 0
            for event, (analysis, error) in zip(selected, results, strict=True):
                if analysis is None:
                    failed += 1
                    reason = error or "AI analizi başarısız"
                    if event in report_events:
                        dispositions.append(
                            AnalysisDisposition(
                                event_id=event.event_id, status="failed", reason=reason
                            )
                        )
                    if not dry_run:
                        self.state.mark_analysis(event, "failed", at_utc, reason=reason)
                else:
                    analysis_by_event[event.event_id] = analysis
                    newly_completed[event_fingerprint(event)] = (event, analysis)
                    if event in report_events:
                        dispositions.append(
                            AnalysisDisposition(
                                event_id=event.event_id,
                                status="completed",
                                reason="Şema ve kaynak doğrulaması tamamlandı.",
                            )
                        )
                    if not dry_run:
                        self.state.mark_analysis(event, "completed", at_utc, analysis=analysis)
            ai_reason = f"{failed} olayın AI analizi retry sonrası başarısız." if failed else None
            statuses.append(
                ProviderStatus(provider=self.llm.name, success=failed == 0, warning=ai_reason)
            )
        else:
            statuses.append(ProviderStatus(provider=self.llm.name, success=True))
        publication_pairs = self.state.completed_unreported_analyses(cadence)
        publication_by_fingerprint = {
            event_fingerprint(event): (event, analysis) for event, analysis in publication_pairs
        }
        publication_by_fingerprint.update(newly_completed)
        analysis_publication_events = [
            event for event, _analysis in publication_by_fingerprint.values()
        ]
        analyses = [analysis for _event, analysis in publication_by_fingerprint.values()]
        factual_fingerprints = {event_fingerprint(event) for event in report_events}
        late_analysis_events = [
            event
            for fingerprint, (event, _analysis) in publication_by_fingerprint.items()
            if fingerprint not in factual_fingerprints
        ]

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
                    warnings.append(f"{label}: {warning}")
                    continue
                if isinstance(result, ProviderResult):
                    self._status_from_result(
                        f"{label} — {provider.name}", result, statuses, warnings
                    )
                    successful_checkpoints.update(
                        {scope: at_utc for scope in result.successful_scopes}
                    )
                    quotes.update(result.data)
                else:
                    statuses.append(
                        ProviderStatus(provider=f"{label} — {provider.name}", success=True)
                    )
                    fx_quote = result
                    successful_checkpoints["fx:USDTRY"] = at_utc
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
                snapshot.market_session = _market_session(
                    {symbol: quotes[symbol] for symbol in securities if symbol in quotes}
                )
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

        history = self.state.history()
        if snapshot:
            current_quantities = {
                position.symbol: position.quantity for position in snapshot.positions
            }
            if history and history[-1].get("position_quantities"):
                previous = history[-1]
                previous_date = date.fromisoformat(
                    str(previous.get("market_session") or pd.Timestamp(previous["as_of"]).date())
                )
                relevant_transactions = [
                    transaction
                    for transaction in self.portfolio.transactions
                    if previous_date
                    < transaction.date
                    <= (snapshot.market_session or at_utc.date())
                ]
                warnings.extend(
                    quantity_change_warnings(
                        previous["position_quantities"],
                        current_quantities,
                        relevant_transactions,
                    )
                )
            session = (snapshot.market_session or snapshot.as_of.date()).isoformat()
            history = [row for row in history if row.get("market_session") != session]
            history.append(
                {
                    "market_session": session,
                    "as_of": snapshot.as_of.isoformat(),
                    "total_value_usd": snapshot.total_value_usd,
                    "total_value_try": snapshot.total_value_try,
                    "market_prices_usd": snapshot.market_prices_usd,
                    "position_values_usd": {
                        position.symbol: position.value_usd for position in snapshot.positions
                    },
                    "position_quantities": current_quantities,
                }
            )
            history.sort(key=lambda row: str(row.get("market_session") or row["as_of"]))
        period_days = 7 if cadence is Cadence.WEEKLY else 31
        if cadence is not Cadence.DAILY:
            cutoff = at_utc - timedelta(days=period_days)
            history = [
                record
                for record in history
                if pd.Timestamp(record["as_of"]).to_pydatetime().astimezone(UTC) >= cutoff
            ]
            first_history_date = (
                pd.Timestamp(history[0]["as_of"]).date() if history else cutoff.date()
            )
            period_transactions = [
                transaction
                for transaction in self.portfolio.transactions
                if transaction.date >= max(cutoff.date(), first_history_date)
            ]
        else:
            period_transactions = []
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
            events=report_events,
            analyses=analyses,
            late_analysis_events=late_analysis_events,
            analysis_dispositions=dispositions,
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
            self.state.commit_success(
                cadence,
                at_utc,
                report_id,
                snapshot,
                report_events,
                analysis_publication_events,
                successful_checkpoints,
            )
            checkpoint_updated = True

        telegram_delivered = False
        if self.delivery and not dry_run:
            reference = (
                f"{self.report_base_url.rstrip('/')}/{html_path.name}"
                if self.report_base_url
                else str(html_path)
            )
            self.state.enqueue_delivery(
                report_id,
                telegram_summary(context, reference),
                [event_fingerprint(event) for event in report_events],
                [event_fingerprint(event) for event in analysis_publication_events],
                at_utc,
            )
            for item in self.state.pending_deliveries(at_utc):
                try:
                    await self.delivery.deliver(item["text"])
                    self.state.mark_delivery(item["report_id"], success=True, at=at_utc)
                    telegram_delivered = telegram_delivered or item["report_id"] == report_id
                except Exception as exc:
                    warning = safe_provider_error(exc)
                    self.state.mark_delivery(
                        item["report_id"], success=False, at=at_utc, error=warning
                    )
                    LOGGER.warning("Telegram gönderimi başarısız: %s", warning)

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
