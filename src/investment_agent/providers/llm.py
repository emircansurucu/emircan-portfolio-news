from __future__ import annotations

import json

from pydantic import ValidationError

from investment_agent.models import AIEventAnalysis, MaterialEvent
from investment_agent.providers.base import NonRetryableLLMError

SYSTEM_PROMPT = """Sen Türkçe yazan bir yatırım araştırma asistanısın.
Kurallar:
- Doğrulanmış olguları AI çıkarımlarından açıkça ayır.
- Al, sat veya tut önerisi verme; kişiselleştirilmiş yatırım tavsiyesi üretme.
- Kaynak kaydında bulunmayan sayı, tarih, ölçüt veya URL üretme.
- Yalnızca sağlanan kaynak URL'lerini kullan.
- Belirsizlikleri açıkça belirt.
- fact_summary_tr yalnızca sağlanan doğrulanmış kaydın kısa Türkçe özetidir.
- interpretation_tr cümlesini 'AI yorumu:' ifadesiyle başlat.
- possible_portfolio_relevance_tr için 'Yatırım tezini etkileyebilecek gelişme:' veya
  'İzlenmesi gereken risk:' ifadelerinden uygun olanıyla başla.
Çıktı yalnızca istenen JSON şemasına uymalıdır.
"""


class OpenAILLMProvider:
    name = "OpenAI"

    def __init__(self, api_key: str, model: str) -> None:
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def analyze(self, event: MaterialEvent) -> AIEventAnalysis:
        source_url = str(event.source.url)
        payload = {
            "event_id": event.event_id,
            "symbol": event.symbol,
            "title": event.title,
            "verified_summary": event.summary,
            "event_type": event.event_type,
            "occurred_at": event.occurred_at.isoformat(),
            "source": {
                "url": source_url,
                "provider": event.source.provider,
                "is_primary": event.source.is_primary,
            },
        }
        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = await self.client.responses.create(
                    model=self.model,
                    instructions=SYSTEM_PROMPT,
                    input=json.dumps(payload, ensure_ascii=False),
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "investment_event_analysis",
                            "schema": AIEventAnalysis.model_json_schema(),
                            "strict": True,
                        }
                    },
                )
                analysis = AIEventAnalysis.model_validate_json(response.output_text)
                if {str(url) for url in analysis.source_urls} - {source_url}:
                    raise ValueError("LLM supplied a URL absent from source records")
                if (
                    analysis.event_id != event.event_id
                    or analysis.symbol != event.symbol
                    or analysis.event_type != event.event_type
                ):
                    raise ValueError("LLM changed deterministic event fields")
                if analysis.primary_source_verified != event.source.is_primary:
                    raise ValueError("LLM changed source verification state")
                return analysis
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
        raise NonRetryableLLMError(
            "LLM output failed schema/provenance validation twice"
        ) from last_error
