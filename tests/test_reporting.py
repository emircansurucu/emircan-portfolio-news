from datetime import UTC, datetime

from investment_agent.models import Cadence, MaterialEvent, ReportContext, SourceRecord
from investment_agent.reporting import ReportRenderer


def test_markdown_and_html_report_generation(tmp_path):
    context = ReportContext(
        cadence=Cadence.DAILY,
        as_of=datetime(2026, 8, 21, tzinfo=UTC),
        report_id="daily-test",
        ai_unavailable_reason="Anahtar yok",
    )
    markdown, html = ReportRenderer(tmp_path).render(context)
    assert "yatırım tavsiyesi değildir" in markdown.read_text(encoding="utf-8")
    assert "<!doctype html>" in html.read_text(encoding="utf-8")


def test_generated_html_sanitizes_event_controlled_markup(tmp_path):
    now = datetime(2026, 8, 21, tzinfo=UTC)
    source = SourceRecord(
        title='<img src=x onerror="alert(1)">',
        url="https://example.com/event",
        published_at=now,
        retrieved_at=now,
        provider='<svg onload="alert(2)">provider</svg>',
        is_primary=True,
    )
    event = MaterialEvent(
        event_id="malicious",
        symbol="AAA",
        title='<script>alert("title")</script><b onclick="evil()">Başlık</b>',
        summary='<img src=x onerror="alert(3)"><a href="javascript:evil()">özet</a>',
        occurred_at=now,
        source=source,
    )
    context = ReportContext(
        cadence=Cadence.DAILY,
        as_of=now,
        report_id="malicious-report",
        events=[event],
    )
    _, html_path = ReportRenderer(tmp_path).render(context)
    generated = html_path.read_text(encoding="utf-8").lower()
    assert "<script" not in generated
    assert "onerror" not in generated
    assert "onclick" not in generated
    assert "onload" not in generated
    assert "javascript:" not in generated
