from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from investment_agent.models import MaterialEvent, SourceRecord
from investment_agent.providers.base import ProviderResult

TRACKED_FORMS = ["10-K", "10-Q", "8-K", "4", "S-3", "424B1", "424B2", "424B3", "424B5"]


class EdgarToolsSecFilingsProvider:
    name = "SEC EDGAR (EdgarTools)"

    def __init__(self, identity: str | None) -> None:
        self.identity = identity

    @staticmethod
    def _to_datetime(value: Any) -> datetime:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return result.replace(tzinfo=result.tzinfo or UTC)

    def _get_sync(
        self, symbols: list[str], since: datetime | None
    ) -> ProviderResult[list[MaterialEvent]]:
        if not self.identity:
            raise RuntimeError("SEC_IDENTITY yapılandırılmamış (Ad Soyad e-posta)")
        from edgar import Company, set_identity

        set_identity(self.identity)
        events: list[MaterialEvent] = []
        warnings: list[str] = []
        successful_scopes: list[str] = []
        for symbol in symbols:
            try:
                company = Company(symbol)
                filings = company.get_filings(form=TRACKED_FORMS)
                for filing in list(filings)[:100]:
                    filed_at = self._to_datetime(
                        getattr(filing, "filing_date", None) or filing.date_of_filing
                    )
                    if since and filed_at <= since.astimezone(UTC):
                        continue
                    accession = str(getattr(filing, "accession_no", "") or filing.accession_number)
                    form = str(getattr(filing, "form", "unknown"))
                    url = str(
                        getattr(filing, "filing_url", "") or getattr(filing, "homepage_url", "")
                    )
                    if not url:
                        url = f"https://www.sec.gov/edgar/search/#/q={accession}"
                    events.append(
                        MaterialEvent(
                            event_id=f"sec:{accession}",
                            symbol=symbol,
                            title=f"{symbol} {form} başvurusu (yalnız filing tespiti)",
                            summary=(
                                f"SEC EDGAR üzerinde {form} formu tespit edildi. Filing içeriği "
                                "ve yapılandırılmış finansal olgular henüz çıkarılmadı."
                            ),
                            event_type="filing",
                            occurred_at=filed_at,
                            accession_number=accession,
                            form=form,
                            source=SourceRecord(
                                title=f"SEC {form} — {accession}",
                                url=url,
                                published_at=filed_at,
                                retrieved_at=datetime.now(UTC),
                                provider=self.name,
                                is_primary=True,
                            ),
                        )
                    )
                successful_scopes.append(f"sec:{symbol}")
            except Exception as exc:
                warnings.append(
                    f"{symbol}: {type(exc).__name__} nedeniyle SEC taraması tamamlanamadı"
                )
        return ProviderResult(events, warnings=warnings, successful_scopes=successful_scopes)

    async def get_filings(
        self, symbols: list[str], since: datetime | None
    ) -> ProviderResult[list[MaterialEvent]]:
        return await asyncio.to_thread(self._get_sync, symbols, since)
