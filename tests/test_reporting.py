from datetime import UTC, datetime

from investment_agent.models import Cadence, ReportContext
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
