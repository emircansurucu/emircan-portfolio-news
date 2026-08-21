import pytest
from pydantic import ValidationError

from investment_agent.models import AIEventAnalysis


def test_llm_schema_validation():
    valid = {
        "event_id": "event-1",
        "symbol": "RKLB",
        "fact_summary_tr": "Doğrulanmış olay.",
        "interpretation_tr": "AI yorumu: Etki belirsiz.",
        "possible_portfolio_relevance_tr": "İzlenmesi gereken risk: Finansman.",
        "event_type": "financing",
        "materiality": "high",
        "confidence": "medium",
        "primary_source_verified": True,
        "source_urls": ["https://www.sec.gov/example"],
        "uncertainties": ["Koşullar açıklanmadı"],
    }
    assert AIEventAnalysis.model_validate(valid).symbol == "RKLB"
    with pytest.raises(ValidationError):
        AIEventAnalysis.model_validate({**valid, "materiality": "urgent"})
