from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class Cadence(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class AssetType(StrEnum):
    STOCK = "stock"
    ETF = "etf"
    PRECIOUS_METAL = "precious_metal"
    CASH = "cash"


class TransactionType(StrEnum):
    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"
    COMMISSION = "commission"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"


class Position(BaseModel):
    symbol: str
    asset_type: AssetType
    quantity: float = Field(ge=0)
    average_cost_usd: float | None = Field(default=None, ge=0)
    unit: Literal["share", "gram", "usd"] = "share"


class Transaction(BaseModel):
    date: date
    type: TransactionType
    symbol: str | None = None
    quantity: float | None = None
    price_usd: float | None = None
    amount_usd: float | None = None
    commission_usd: float = Field(default=0, ge=0)
    note: str | None = None


class PortfolioConfig(BaseModel):
    base_currency: Literal["TRY"] = "TRY"
    positions: list[Position]
    transactions: list[Transaction] = Field(default_factory=list)
    benchmark_symbols: list[str] = Field(default_factory=lambda: ["SP500", "NASDAQ100", "DXY"])

    @model_validator(mode="after")
    def unique_symbols(self) -> PortfolioConfig:
        symbols = [position.symbol for position in self.positions]
        if len(symbols) != len(set(symbols)):
            raise ValueError("Portfolio position symbols must be unique")
        return self


class SourceRecord(BaseModel):
    title: str
    url: HttpUrl
    published_at: datetime
    retrieved_at: datetime
    provider: str
    is_primary: bool = False


class PriceQuote(BaseModel):
    symbol: str
    price_usd: float = Field(gt=0)
    previous_close_usd: float | None = Field(default=None, gt=0)
    as_of: datetime
    source: SourceRecord
    delayed: bool = False

    @property
    def daily_return(self) -> float | None:
        if self.previous_close_usd is None:
            return None
        return self.price_usd / self.previous_close_usd - 1


class MacroObservation(BaseModel):
    series_id: str
    name_tr: str
    value: float
    unit: str
    observed_at: datetime
    source: SourceRecord


EventType = Literal["contract", "earnings", "filing", "launch", "regulatory", "financing", "other"]


class MaterialEvent(BaseModel):
    event_id: str
    symbol: str
    title: str
    summary: str | None = None
    event_type: EventType = "other"
    occurred_at: datetime
    source: SourceRecord
    accession_number: str | None = None
    form: str | None = None


class AIEventAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    symbol: str
    fact_summary_tr: str
    interpretation_tr: str
    possible_portfolio_relevance_tr: str
    event_type: EventType
    materiality: Literal["low", "medium", "high"]
    confidence: Literal["low", "medium", "high"]
    primary_source_verified: bool
    source_urls: list[HttpUrl]
    uncertainties: list[str]

    @field_validator("interpretation_tr")
    @classmethod
    def interpretation_is_labeled(cls, value: str) -> str:
        if not value.startswith("AI yorumu:"):
            raise ValueError("interpretation_tr must begin with 'AI yorumu:'")
        return value

    @field_validator("possible_portfolio_relevance_tr")
    @classmethod
    def relevance_is_labeled(cls, value: str) -> str:
        prefixes = ("Yatırım tezini etkileyebilecek gelişme:", "İzlenmesi gereken risk:")
        if not value.startswith(prefixes):
            raise ValueError("portfolio relevance must use an approved Turkish label")
        return value


class PositionValuation(BaseModel):
    symbol: str
    asset_type: AssetType
    quantity: float
    price_usd: float
    value_usd: float
    value_try: float
    weight: float
    cost_basis_usd: float | None = None
    unrealized_pnl_usd: float | None = None
    daily_contribution_pct: float | None = None


class PortfolioSnapshot(BaseModel):
    as_of: datetime
    usdtry: float = Field(gt=0)
    total_value_usd: float = Field(ge=0)
    total_value_try: float = Field(ge=0)
    daily_return_pct: float | None = None
    positions: list[PositionValuation]
    market_prices_usd: dict[str, float] = Field(default_factory=dict)


class ProviderStatus(BaseModel):
    provider: str
    success: bool
    warning: str | None = None


class ReportContext(BaseModel):
    cadence: Cadence
    as_of: datetime
    since: datetime | None = None
    market_data_at: datetime | None = None
    snapshot: PortfolioSnapshot | None = None
    events: list[MaterialEvent] = Field(default_factory=list)
    analyses: list[AIEventAnalysis] = Field(default_factory=list)
    macro: list[MacroObservation] = Field(default_factory=list)
    market_sources: list[SourceRecord] = Field(default_factory=list)
    market_quotes: list[PriceQuote] = Field(default_factory=list)
    providers: list[ProviderStatus] = Field(default_factory=list)
    freshness_warnings: list[str] = Field(default_factory=list)
    ai_unavailable_reason: str | None = None
    period_metrics: dict[str, float | None] = Field(default_factory=dict)
    upcoming_events: list[str] = Field(default_factory=list)
    report_id: str


class RunResult(BaseModel):
    cadence: Cadence
    report_id: str
    markdown_path: str
    html_path: str
    checkpoint_updated: bool
    telegram_delivered: bool
    skipped_market_reporting: bool = False
    provider_failures: list[str] = Field(default_factory=list)
