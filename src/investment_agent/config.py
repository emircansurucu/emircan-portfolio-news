from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from investment_agent.models import PortfolioConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: str = "openai"
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    fred_api_key: SecretStr | None = None
    sec_identity: str | None = None
    ir_feeds_json: str = "{}"
    portfolio_path: Path = Path("portfolio.yaml")
    data_dir: Path = Path("data")
    reports_dir: Path = Path("reports")
    report_base_url: str | None = None
    timezone: str = "Europe/Istanbul"
    http_timeout_seconds: float = Field(default=20.0, gt=0)

    @field_validator("llm_provider")
    @classmethod
    def validate_llm_provider(cls, value: str) -> str:
        if value not in {"openai", "anthropic", "none"}:
            raise ValueError("LLM_PROVIDER must be openai, anthropic, or none")
        return value

    @field_validator("ir_feeds_json", mode="before")
    @classmethod
    def default_blank_ir_feeds(cls, value: object) -> object:
        return "{}" if value is None or value == "" else value

    @property
    def ir_feeds(self) -> dict[str, list[str]]:
        value: Any = json.loads(self.ir_feeds_json)
        if not isinstance(value, dict):
            raise ValueError("IR_FEEDS_JSON must be a JSON object")
        return {str(symbol): [str(url) for url in urls] for symbol, urls in value.items()}


def load_portfolio(path: Path) -> PortfolioConfig:
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return PortfolioConfig.model_validate(raw)
