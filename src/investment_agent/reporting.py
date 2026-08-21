from __future__ import annotations

import os
import tempfile
from pathlib import Path

import markdown as markdown_converter
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from investment_agent.models import ReportContext


class ReportRenderer:
    def __init__(self, reports_dir: Path) -> None:
        templates = Path(__file__).parent / "templates"
        self.environment = Environment(
            loader=FileSystemLoader(templates),
            autoescape=select_autoescape(enabled_extensions=("html",)),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.reports_dir = reports_dir
        reports_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _atomic_text(path: Path, content: str) -> None:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise

    def render(self, context: ReportContext) -> tuple[Path, Path]:
        filters = self.environment.filters
        filters["money"] = lambda value: "—" if value is None else f"{value:,.2f}"
        filters["pct"] = lambda value: "—" if value is None else f"{value:+.2f}%"
        filters["dt"] = lambda value: "—" if value is None else value.strftime("%Y-%m-%d %H:%M %Z")
        markdown = self.environment.get_template("report.md.j2").render(report=context)
        body = markdown_converter.markdown(markdown, extensions=["tables"])
        html = self.environment.get_template("report.html.j2").render(
            report=context, body=Markup(body)
        )
        markdown_path = self.reports_dir / f"{context.report_id}.md"
        html_path = self.reports_dir / f"{context.report_id}.html"
        self._atomic_text(markdown_path, markdown)
        self._atomic_text(html_path, html)
        return markdown_path, html_path


def telegram_summary(context: ReportContext, report_reference: str) -> str:
    lines = [f"{context.cadence.value.upper()} portföy araştırma raporu"]
    if context.snapshot:
        change = (
            context.snapshot.daily_return_pct
            if context.cadence.value == "daily"
            else context.period_metrics.get("investment_return_pct")
        )
        lines.extend(
            [
                f"Değer: ${context.snapshot.total_value_usd:,.2f} / ₺{context.snapshot.total_value_try:,.2f}",
                f"{context.cadence.value.capitalize()} değişim: {change:+.2f}%"
                if change is not None
                else "Değişim: doğrulanamadı",
            ]
        )
        contributions = []
        for position in context.snapshot.positions:
            value = (
                position.daily_contribution_pct
                if context.cadence.value == "daily"
                else context.period_metrics.get(f"period_{position.symbol}_contribution_pct")
            )
            if value is not None:
                contributions.append((position.symbol, value))
        ranked = sorted(contributions, key=lambda item: item[1])
        if ranked:
            lines.append(
                f"Katkı: + {ranked[-1][0]} {ranked[-1][1]:+.2f} puan; "
                f"- {ranked[0][0]} {ranked[0][1]:+.2f} puan"
            )
    high = [analysis for analysis in context.analyses if analysis.materiality == "high"][:3]
    lines.extend(f"• {item.symbol}: {item.fact_summary_tr}" for item in high)
    failures = [status.provider for status in context.providers if not status.success]
    if failures:
        lines.append("Uyarı — başarısız sağlayıcılar: " + ", ".join(failures))
    lines.append(f"Tam rapor: {report_reference}")
    lines.append("Bilgilendirme amaçlıdır; yatırım tavsiyesi değildir.")
    return "\n".join(lines)
