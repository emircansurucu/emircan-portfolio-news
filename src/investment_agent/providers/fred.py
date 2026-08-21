from __future__ import annotations

from datetime import UTC, datetime

import httpx

from investment_agent.models import MacroObservation, SourceRecord
from investment_agent.providers.base import ProviderResult

SERIES = {
    "CPIAUCSL": ("ABD tüketici fiyat endeksi", "endeks"),
    "UNRATE": ("ABD işsizlik oranı", "%"),
    "FEDFUNDS": ("Federal Funds efektif faiz oranı", "%"),
    "DGS10": ("ABD 10 yıllık Hazine tahvili getirisi", "%"),
}


class FredMacroDataProvider:
    name = "FRED"

    def __init__(self, client: httpx.AsyncClient, api_key: str | None) -> None:
        self.client = client
        self.api_key = api_key

    async def get_observations(self) -> ProviderResult[list[MacroObservation]]:
        if not self.api_key:
            raise RuntimeError("FRED_API_KEY yapılandırılmamış")
        observations: list[MacroObservation] = []
        warnings: list[str] = []
        successful_scopes: list[str] = []
        retrieved = datetime.now(UTC)
        for series_id, (name, unit) in SERIES.items():
            try:
                response = await self.client.get(
                    "https://api.stlouisfed.org/fred/series/observations",
                    params={
                        "series_id": series_id,
                        "api_key": self.api_key,
                        "file_type": "json",
                        "sort_order": "desc",
                        "limit": 1,
                    },
                )
                if response.is_error:
                    raise RuntimeError(f"HTTP {response.status_code}")
                item = response.json()["observations"][0]
                observed = datetime.fromisoformat(item["date"]).replace(tzinfo=UTC)
                observations.append(
                    MacroObservation(
                        series_id=series_id,
                        name_tr=name,
                        value=float(item["value"]),
                        unit=unit,
                        observed_at=observed,
                        source=SourceRecord(
                            title=f"FRED {series_id}",
                            url=f"https://fred.stlouisfed.org/series/{series_id}",
                            published_at=observed,
                            retrieved_at=retrieved,
                            provider=self.name,
                            is_primary=True,
                        ),
                    )
                )
                successful_scopes.append(f"fred:{series_id}")
            except Exception as exc:
                detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
                warnings.append(f"{series_id}: {detail[:120]}")
        return ProviderResult(observations, warnings=warnings, successful_scopes=successful_scopes)
