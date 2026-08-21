from __future__ import annotations

from datetime import datetime, timedelta

from investment_agent.models import (
    AIEventAnalysis,
    AssetType,
    Cadence,
    MaterialEvent,
    PortfolioSnapshot,
    PositionValuation,
    SourceRecord,
)
from investment_agent.state import JsonStateRepository, event_fingerprint


def event(now: datetime) -> MaterialEvent:
    return MaterialEvent(
        event_id="filing-1",
        symbol="MSFT",
        title="10-Q filed",
        event_type="filing",
        occurred_at=now,
        accession_number="0001-26-000001",
        source=SourceRecord(
            title="SEC filing",
            url="https://www.sec.gov/example",
            published_at=now,
            retrieved_at=now,
            provider="SEC",
            is_primary=True,
        ),
    )


def test_event_deduplication_and_checkpoint(tmp_path, now):
    repository = JsonStateRepository(tmp_path)
    item = event(now)
    assert not repository.is_processed(item, Cadence.DAILY)
    repository.commit_success(Cadence.DAILY, now, "daily-1", None, [item])
    assert repository.is_processed(item, Cadence.DAILY)
    assert not repository.is_processed(item, Cadence.WEEKLY)
    assert repository.unreported_events(Cadence.WEEKLY, None, now - timedelta(days=1)) == [item]
    assert repository.last_success(Cadence.DAILY) == now
    assert event_fingerprint(item) == "sec:0001-26-000001"
    repository.commit_success(Cadence.DAILY, now, "daily-2", None, [item])
    assert len(repository.events_path.read_text().splitlines()) == 1


def snapshot(now, value=1000):
    return PortfolioSnapshot(
        as_of=now,
        market_session=now.date(),
        usdtry=40,
        total_value_usd=value,
        total_value_try=value * 40,
        positions=[
            PositionValuation(
                symbol="AAA",
                asset_type=AssetType.STOCK,
                quantity=10,
                price_usd=value / 10,
                value_usd=value,
                value_try=value * 40,
                weight=1,
            )
        ],
        market_prices_usd={"AAA": value / 10},
    )


def analysis(item):
    return AIEventAnalysis(
        event_id=item.event_id,
        symbol=item.symbol,
        fact_summary_tr="Doğrulanmış olay.",
        interpretation_tr="AI yorumu: Etki belirsiz.",
        possible_portfolio_relevance_tr="İzlenmesi gereken risk: Belirsizlik.",
        event_type=item.event_type,
        materiality="low",
        confidence="medium",
        primary_source_verified=True,
        source_urls=[item.source.url],
        uncertainties=[],
    )


def test_canonical_history_is_upserted_per_market_session(tmp_path, now):
    repository = JsonStateRepository(tmp_path)
    repository.commit_success(Cadence.DAILY, now, "daily", snapshot(now), [])
    repository.commit_success(Cadence.WEEKLY, now, "weekly", snapshot(now, 1100), [])
    rows = repository.history()
    assert len(rows) == 1
    assert rows[0]["total_value_usd"] == 1100
    assert rows[0]["report_id"] == "weekly"


def test_event_lifecycle_and_retryable_outbox_are_separate(tmp_path, now):
    repository = JsonStateRepository(tmp_path)
    item = event(now)
    repository.record_discovered([item], now)
    assert repository.analysis_state(item) == "pending"
    repository.mark_analysis(item, "failed", now, reason="temporary")
    assert repository.analysis_state(item) == "failed"
    assert repository.analysis_for(item) is None
    repository.mark_analysis(item, "completed", now, analysis=analysis(item))
    assert repository.analysis_for(item) is not None
    assert not repository.is_processed(item, Cadence.DAILY)

    repository.commit_success(Cadence.DAILY, now, "daily-1", None, [item])
    assert repository.is_processed(item, Cadence.DAILY)
    repository.enqueue_delivery("daily-1", "message", [event_fingerprint(item)], now)
    assert len(repository.pending_deliveries(now)) == 1
    repository.mark_delivery("daily-1", success=False, at=now, error="offline")
    assert repository.pending_deliveries(now) == []
    retry_at = now + timedelta(minutes=3)
    assert len(repository.pending_deliveries(retry_at)) == 1
    repository.mark_delivery("daily-1", success=True, at=retry_at)
    assert repository.pending_deliveries(retry_at) == []
    lifecycle = repository._state()["event_lifecycle"][event_fingerprint(item)]
    assert lifecycle["discovered_at"]
    assert lifecycle["reported"]["daily"]
    assert lifecycle["ai"]["status"] == "completed"
    assert lifecycle["deliveries"][0]["report_id"] == "daily-1"


def test_state_recovers_from_last_known_good_backup(tmp_path, now):
    repository = JsonStateRepository(tmp_path)
    repository.commit_success(Cadence.DAILY, now, "first", None, [])
    later = now + timedelta(days=1)
    repository.commit_success(Cadence.DAILY, later, "second", None, [])
    repository.state_path.write_text("{broken", encoding="utf-8")
    assert repository.last_success(Cadence.DAILY) == now
