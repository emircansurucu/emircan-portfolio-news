from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

import pytest

from investment_agent.models import AIEventAnalysis, Cadence, MaterialEvent, SourceRecord
from investment_agent.providers.base import ProviderResult
from investment_agent.reporting import ReportRenderer
from investment_agent.service import InvestmentAgent
from investment_agent.state import JsonStateRepository, event_fingerprint
from tests.conftest import make_quote


class Market:
    name = "test market"

    async def get_quotes(self, symbols):
        return ProviderResult(
            {symbol: make_quote(symbol, 100, 95, NOW) for symbol in symbols},
            successful_scopes=[f"market:{symbol}" for symbol in symbols],
        )


class Metals:
    name = "test metals"

    async def get_metals(self):
        return ProviderResult(
            {"GOLD": make_quote("GOLD", 50, 49, NOW)},
            successful_scopes=["metals:GOLD"],
        )


class Fx:
    name = "test fx"

    async def get_usdtry(self):
        return make_quote("USDTRY", 32, 31.9, NOW)


class Events:
    name = "test events"

    async def get_filings(self, symbols, since):
        return ProviderResult([], successful_scopes=[f"sec:{symbol}" for symbol in symbols])

    async def get_announcements(self, symbols, since):
        return ProviderResult([], successful_scopes=["ir:test"])


class FailingMacro:
    name = "failing macro"

    async def get_observations(self):
        raise RuntimeError("offline")


class ExplodingRenderer:
    def render(self, context):
        raise RuntimeError("disk full")


NOW = datetime(2026, 8, 20, 20, tzinfo=UTC)


def agent(tmp_path, portfolio, renderer):
    events = Events()
    return InvestmentAgent(
        portfolio=portfolio,
        state=JsonStateRepository(tmp_path / "data"),
        renderer=renderer,
        market=Market(),
        metals=Metals(),
        fx=Fx(),
        sec=events,
        news=events,
        macro=FailingMacro(),
        llm=None,
    )


@pytest.mark.asyncio
async def test_partial_provider_failure_still_generates_report(tmp_path, portfolio):
    runner = agent(tmp_path, portfolio, ReportRenderer(tmp_path / "reports"))
    result = await runner.run(Cadence.WEEKLY, now=NOW)
    assert "failing macro" in result.provider_failures
    assert result.checkpoint_updated
    assert (tmp_path / "reports" / f"{result.report_id}.md").exists()
    assert runner.state.last_success(Cadence.WEEKLY) == NOW


@pytest.mark.asyncio
async def test_failed_report_does_not_update_checkpoint(tmp_path, portfolio):
    runner = agent(tmp_path, portfolio, ExplodingRenderer())
    with pytest.raises(RuntimeError, match="disk full"):
        await runner.run(Cadence.WEEKLY, now=NOW)
    assert runner.state.last_success(Cadence.WEEKLY) is None


@pytest.mark.asyncio
async def test_dry_run_never_updates_checkpoint(tmp_path, portfolio):
    class Delivery:
        calls = 0

        async def deliver(self, text):
            self.calls += 1

    runner = agent(tmp_path, portfolio, ReportRenderer(tmp_path / "reports"))
    delivery = Delivery()
    runner.delivery = delivery
    result = await runner.run(Cadence.DAILY, dry_run=True, now=NOW)
    assert not result.checkpoint_updated
    assert runner.state.last_success(Cadence.DAILY) is None
    assert delivery.calls == 0


def material_event(number: int, *, filing: bool = False) -> MaterialEvent:
    return MaterialEvent(
        event_id=f"event-{number}",
        symbol="AAA",
        title=f"Event {number}",
        summary="Verified summary",
        event_type="filing" if filing else "other",
        occurred_at=NOW,
        accession_number=f"accession-{number}" if filing else None,
        form="8-K" if filing else None,
        source=SourceRecord(
            title=f"Source {number}",
            url=f"https://example.com/event/{number}",
            published_at=NOW,
            retrieved_at=NOW,
            provider="official",
            is_primary=True,
        ),
    )


class SecBatch:
    name = "SEC test"

    def __init__(self, events=None):
        self.events = events or []

    async def get_filings(self, symbols, since):
        return ProviderResult(self.events, successful_scopes=["sec:AAA"])


class NewsBatch:
    name = "IR test"
    scopes: ClassVar[list[str]] = ["ir:AAA:test"]

    def __init__(self, events=None, *, warnings=None, successful=True):
        self.events = events or []
        self.warnings = warnings or []
        self.successful = successful

    async def get_announcements(self, symbols, since):
        return ProviderResult(
            self.events,
            warnings=self.warnings,
            successful_scopes=["ir:AAA:test"] if self.successful else [],
        )


class EmptyMacro:
    name = "macro test"

    async def get_observations(self):
        return ProviderResult([], successful_scopes=["fred:test"])


def event_agent(tmp_path, portfolio, *, sec, news, llm=None, delivery=None, **limits):
    return InvestmentAgent(
        portfolio=portfolio,
        state=JsonStateRepository(tmp_path / "data"),
        renderer=ReportRenderer(tmp_path / "reports"),
        market=Market(),
        metals=Metals(),
        fx=Fx(),
        sec=sec,
        news=news,
        macro=EmptyMacro(),
        llm=llm,
        delivery=delivery,
        **limits,
    )


def make_analysis(item: MaterialEvent) -> AIEventAnalysis:
    return AIEventAnalysis(
        event_id=item.event_id,
        symbol=item.symbol,
        fact_summary_tr="Doğrulanmış olay.",
        interpretation_tr="AI yorumu: Etki belirsiz.",
        possible_portfolio_relevance_tr="İzlenmesi gereken risk: Belirsizlik.",
        event_type=item.event_type,
        materiality="medium",
        confidence="medium",
        primary_source_verified=True,
        source_urls=[item.source.url],
        uncertainties=[],
    )


class FlakyLLM:
    name = "test LLM"

    def __init__(self, failures):
        self.failures = failures
        self.calls = 0
        self.active = 0
        self.max_active = 0

    async def analyze(self, item):
        import asyncio

        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.001)
        self.active -= 1
        if self.calls <= self.failures:
            raise RuntimeError("temporary AI failure")
        return make_analysis(item)


@pytest.mark.asyncio
async def test_ai_retries_and_sec_detection_is_never_sent_to_llm(tmp_path, portfolio):
    filing = material_event(1, filing=True)
    announcement = material_event(2)
    llm = FlakyLLM(failures=2)
    runner = event_agent(
        tmp_path,
        portfolio,
        sec=SecBatch([filing]),
        news=NewsBatch([announcement]),
        llm=llm,
        llm_retry_attempts=3,
        llm_retry_backoff_seconds=0,
    )
    result = await runner.run(Cadence.WEEKLY, now=NOW)
    assert llm.calls == 3
    assert runner.state.analysis_state(announcement) == "completed"
    assert runner.state.analysis_state(filing) == "skipped"
    report = (tmp_path / "reports" / f"{result.report_id}.md").read_text(encoding="utf-8")
    assert "yalnız filing tespitidir" in report


@pytest.mark.asyncio
async def test_ai_cap_bounded_concurrency_and_deferred_state(tmp_path, portfolio):
    events = [material_event(number) for number in range(12)]
    llm = FlakyLLM(failures=0)
    runner = event_agent(
        tmp_path,
        portfolio,
        sec=SecBatch(),
        news=NewsBatch(events),
        llm=llm,
        max_llm_events_per_run=10,
        llm_max_concurrency=2,
        llm_retry_attempts=1,
    )
    await runner.run(Cadence.WEEKLY, now=NOW)
    assert llm.calls == 10
    assert llm.max_active <= 2
    assert sum(runner.state.analysis_state(item) == "deferred" for item in events) == 2

    second = await runner.run(Cadence.WEEKLY, now=NOW.replace(day=21))
    second_report = (tmp_path / "reports" / f"{second.report_id}.md").read_text(encoding="utf-8")
    assert llm.calls == 12
    assert second_report.count("**Sonradan tamamlanan AI yorumu:**") == 2
    assert all(runner.state.analysis_state(item) == "completed" for item in events)

    third = await runner.run(Cadence.WEEKLY, now=NOW.replace(day=22))
    third_report = (tmp_path / "reports" / f"{third.report_id}.md").read_text(encoding="utf-8")
    assert llm.calls == 12
    assert "**Sonradan tamamlanan AI yorumu:**" not in third_report


@pytest.mark.asyncio
async def test_failed_ai_analysis_remains_retryable(tmp_path, portfolio):
    item = material_event(20)
    failing = FlakyLLM(failures=100)
    runner = event_agent(
        tmp_path,
        portfolio,
        sec=SecBatch(),
        news=NewsBatch([item]),
        llm=failing,
        llm_retry_attempts=2,
        llm_retry_backoff_seconds=0,
    )
    first = await runner.run(Cadence.WEEKLY, now=NOW)
    assert runner.state.analysis_state(item) == "failed"
    first_report = (tmp_path / "reports" / f"{first.report_id}.md").read_text(encoding="utf-8")
    assert "Event 20" in first_report
    assert "**Sonradan tamamlanan AI yorumu:**" not in first_report

    runner.llm = FlakyLLM(failures=0)
    second = await runner.run(Cadence.WEEKLY, now=NOW.replace(day=21))
    assert runner.state.analysis_state(item) == "completed"
    second_report = (tmp_path / "reports" / f"{second.report_id}.md").read_text(encoding="utf-8")
    second_html = (tmp_path / "reports" / f"{second.report_id}.html").read_text(encoding="utf-8")
    assert "**Sonradan tamamlanan AI yorumu:** AI yorumu: Etki belirsiz." in second_report
    assert "Sonradan tamamlanan AI yorumu" in second_html
    lifecycle = runner.state._state()["event_lifecycle"][event_fingerprint(item)]
    assert lifecycle["ai"]["reported"]["weekly"]["report_id"] == second.report_id

    third = await runner.run(Cadence.WEEKLY, now=NOW.replace(day=22))
    third_report = (tmp_path / "reports" / f"{third.report_id}.md").read_text(encoding="utf-8")
    third_html = (tmp_path / "reports" / f"{third.report_id}.html").read_text(encoding="utf-8")
    assert "**Sonradan tamamlanan AI yorumu:**" not in third_report
    assert "Sonradan tamamlanan AI yorumu" not in third_html


@pytest.mark.asyncio
async def test_analysis_completed_after_llm_configuration_is_published_once(tmp_path, portfolio):
    item = material_event(21)
    runner = event_agent(
        tmp_path,
        portfolio,
        sec=SecBatch(),
        news=NewsBatch([item]),
        llm=None,
    )
    first = await runner.run(Cadence.WEEKLY, now=NOW)
    first_report = (tmp_path / "reports" / f"{first.report_id}.md").read_text(encoding="utf-8")
    assert "Event 21" in first_report
    assert runner.state.analysis_state(item) == "pending"

    llm = FlakyLLM(failures=0)
    runner.llm = llm
    second = await runner.run(Cadence.WEEKLY, now=NOW.replace(day=21))
    second_report = (tmp_path / "reports" / f"{second.report_id}.md").read_text(encoding="utf-8")
    assert llm.calls == 1
    assert "**Sonradan tamamlanan AI yorumu:** AI yorumu: Etki belirsiz." in second_report

    third = await runner.run(Cadence.WEEKLY, now=NOW.replace(day=22))
    third_report = (tmp_path / "reports" / f"{third.report_id}.md").read_text(encoding="utf-8")
    assert llm.calls == 1
    assert "**Sonradan tamamlanan AI yorumu:**" not in third_report


class FlakyDelivery:
    name = "Telegram test"

    def __init__(self):
        self.fail = True
        self.calls = 0

    async def deliver(self, text):
        self.calls += 1
        if self.fail:
            raise RuntimeError("telegram offline")


@pytest.mark.asyncio
async def test_delivery_outbox_retries_on_later_run(tmp_path, portfolio):
    delivery = FlakyDelivery()
    runner = event_agent(
        tmp_path,
        portfolio,
        sec=SecBatch(),
        news=NewsBatch(),
        delivery=delivery,
    )
    first = await runner.run(Cadence.WEEKLY, now=NOW)
    assert not first.telegram_delivered
    assert runner.state.pending_deliveries(NOW) == []
    delivery.fail = False
    later = NOW.replace(minute=3)
    await runner.run(Cadence.DAILY, now=later)
    outbox = runner.state._state()["outbox"]
    old = next(item for item in outbox if item["report_id"] == first.report_id)
    assert old["status"] == "delivered"
    assert delivery.calls >= 2


@pytest.mark.asyncio
async def test_late_analysis_telegram_delivery_is_retryable(tmp_path, portfolio):
    item = material_event(30)
    delivery = FlakyDelivery()
    delivery.fail = False
    runner = event_agent(
        tmp_path,
        portfolio,
        sec=SecBatch(),
        news=NewsBatch([item]),
        llm=FlakyLLM(failures=100),
        delivery=delivery,
        llm_retry_attempts=1,
        llm_retry_backoff_seconds=0,
    )
    await runner.run(Cadence.WEEKLY, now=NOW)

    runner.llm = FlakyLLM(failures=0)
    delivery.fail = True
    second_at = NOW.replace(day=21)
    second = await runner.run(Cadence.WEEKLY, now=second_at)
    outbox = runner.state._state()["outbox"]
    late_item = next(entry for entry in outbox if entry["report_id"] == second.report_id)
    fingerprint = event_fingerprint(item)
    assert "Sonradan tamamlanan AI yorumu" in late_item["text"]
    assert late_item["analysis_event_ids"] == [fingerprint]
    assert late_item["status"] == "failed"
    lifecycle = runner.state._state()["event_lifecycle"][fingerprint]
    assert lifecycle["ai"]["deliveries"] == []

    delivery.fail = False
    third_at = second_at.replace(minute=3)
    third = await runner.run(Cadence.WEEKLY, now=third_at)
    third_report = (tmp_path / "reports" / f"{third.report_id}.md").read_text(encoding="utf-8")
    outbox = runner.state._state()["outbox"]
    late_item = next(entry for entry in outbox if entry["report_id"] == second.report_id)
    lifecycle = runner.state._state()["event_lifecycle"][fingerprint]
    assert late_item["status"] == "delivered"
    assert lifecycle["ai"]["deliveries"] == [
        {"report_id": second.report_id, "delivered_at": third_at.isoformat()}
    ]
    assert "**Sonradan tamamlanan AI yorumu:**" not in third_report


@pytest.mark.asyncio
async def test_dry_run_does_not_modify_late_analysis_lifecycle(tmp_path, portfolio):
    item = material_event(40)
    runner = event_agent(
        tmp_path,
        portfolio,
        sec=SecBatch(),
        news=NewsBatch([item]),
        llm=FlakyLLM(failures=0),
    )
    runner.state.record_discovered([item], NOW)
    runner.state.mark_analysis(item, "failed", NOW, reason="temporary")
    runner.state.commit_success(Cadence.WEEKLY, NOW, "weekly-existing", None, [item])
    state_before = runner.state.state_path.read_bytes()
    events_before = runner.state.events_path.read_bytes()

    result = await runner.run(Cadence.WEEKLY, dry_run=True, now=NOW.replace(day=21))
    report = (tmp_path / "reports" / f"{result.report_id}.md").read_text(encoding="utf-8")
    assert "**Sonradan tamamlanan AI yorumu:** AI yorumu: Etki belirsiz." in report
    assert runner.state.state_path.read_bytes() == state_before
    assert runner.state.events_path.read_bytes() == events_before
    lifecycle = runner.state._state()["event_lifecycle"][event_fingerprint(item)]
    assert lifecycle["ai"]["status"] == "failed"
    assert lifecycle["ai"]["reported"] == {}


@pytest.mark.asyncio
async def test_only_successful_provider_scopes_advance(tmp_path, portfolio):
    runner = event_agent(
        tmp_path,
        portfolio,
        sec=SecBatch(),
        news=NewsBatch(warnings=["feed failed"], successful=False),
    )
    result = await runner.run(Cadence.WEEKLY, now=NOW)
    assert runner.state.provider_checkpoint("sec:AAA") == NOW
    assert runner.state.provider_checkpoint("ir:AAA:test") is None
    report = (tmp_path / "reports" / f"{result.report_id}.md").read_text(encoding="utf-8")
    assert "IR test: feed failed" in report


@pytest.mark.asyncio
async def test_unexplained_position_quantity_change_is_reported(tmp_path, portfolio):
    first = event_agent(tmp_path, portfolio, sec=SecBatch(), news=NewsBatch())
    await first.run(Cadence.WEEKLY, now=NOW)
    changed = portfolio.model_copy(deep=True)
    changed.positions[0].quantity += 1
    second = event_agent(tmp_path, changed, sec=SecBatch(), news=NewsBatch())
    result = await second.run(Cadence.DAILY, now=NOW.replace(day=21))
    report = (tmp_path / "reports" / f"{result.report_id}.md").read_text(encoding="utf-8")
    assert "Alış/satış kaydı eksik olabilir" in report
