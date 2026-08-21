from __future__ import annotations

from datetime import datetime

from investment_agent.models import Cadence, MaterialEvent, SourceRecord
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
    assert repository.last_success(Cadence.DAILY) == now
    assert event_fingerprint(item) == "sec:0001-26-000001"
    repository.commit_success(Cadence.DAILY, now, "daily-2", None, [item])
    assert len(repository.events_path.read_text().splitlines()) == 1
