from __future__ import annotations

from datetime import UTC, datetime

import pytest

from investment_agent.models import Cadence
from investment_agent.reporting import ReportRenderer
from investment_agent.service import InvestmentAgent
from investment_agent.state import JsonStateRepository
from tests.conftest import make_quote


class Market:
    name = "test market"

    async def get_quotes(self, symbols):
        return {symbol: make_quote(symbol, 100, 95, NOW) for symbol in symbols}


class Metals:
    name = "test metals"

    async def get_metals(self):
        return {"GOLD": make_quote("GOLD", 50, 49, NOW)}


class Fx:
    name = "test fx"

    async def get_usdtry(self):
        return make_quote("USDTRY", 32, 31.9, NOW)


class Events:
    name = "test events"

    async def get_filings(self, symbols, since):
        return []

    async def get_announcements(self, symbols, since):
        return []


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
    runner = agent(tmp_path, portfolio, ReportRenderer(tmp_path / "reports"))
    result = await runner.run(Cadence.DAILY, dry_run=True, now=NOW)
    assert not result.checkpoint_updated
    assert runner.state.last_success(Cadence.DAILY) is None
