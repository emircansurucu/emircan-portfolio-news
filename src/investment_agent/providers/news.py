from __future__ import annotations

import asyncio
import hashlib
import html
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

from investment_agent.models import MaterialEvent, SourceRecord
from investment_agent.providers.base import ProviderResult


class OfficialFeedNewsProvider:
    name = "Resmî yatırımcı ilişkileri beslemeleri"

    def __init__(self, client: httpx.AsyncClient, feeds: dict[str, list[str]]) -> None:
        self.client = client
        self.feeds = feeds

    @property
    def scopes(self) -> list[str]:
        return [
            f"ir:{symbol}:{hashlib.sha256(url.encode()).hexdigest()[:12]}"
            for symbol, urls in self.feeds.items()
            for url in urls
        ]

    @staticmethod
    def _text(element: ElementTree.Element, names: tuple[str, ...]) -> str | None:
        for child in element.iter():
            if child.tag.rsplit("}", 1)[-1] in names and child.text:
                return child.text.strip()
        return None

    @staticmethod
    def _date(value: str | None) -> datetime:
        if not value:
            return datetime.now(UTC)
        try:
            return parsedate_to_datetime(value).astimezone(UTC)
        except (TypeError, ValueError):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)

    @staticmethod
    def _short_summary(value: str | None) -> str | None:
        if not value:
            return None
        plain = re.sub(r"<[^>]+>", " ", html.unescape(value))
        normalized = " ".join(plain.split())
        return normalized[:600] + ("…" if len(normalized) > 600 else "")

    async def _feed(self, symbol: str, url: str, since: datetime | None) -> list[MaterialEvent]:
        response = await self.client.get(url)
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        events: list[MaterialEvent] = []
        for item in root.iter():
            if item.tag.rsplit("}", 1)[-1] not in {"item", "entry"}:
                continue
            title = self._text(item, ("title",)) or "Başlıksız duyuru"
            link = self._text(item, ("link",))
            if not link:
                link_node = next(
                    (child for child in item if child.tag.rsplit("}", 1)[-1] == "link"), None
                )
                link = link_node.attrib.get("href") if link_node is not None else None
            if not link:
                continue
            published = self._date(self._text(item, ("pubDate", "published", "updated")))
            if since and published <= since.astimezone(UTC):
                continue
            summary = self._short_summary(self._text(item, ("description", "summary")))
            digest = hashlib.sha256(f"{symbol}|{title}|{link}".encode()).hexdigest()
            events.append(
                MaterialEvent(
                    event_id=f"ir:{digest}",
                    symbol=symbol,
                    title=title,
                    summary=summary,
                    occurred_at=published,
                    source=SourceRecord(
                        title=title,
                        url=link,
                        published_at=published,
                        retrieved_at=datetime.now(UTC),
                        provider=self.name,
                        is_primary=True,
                    ),
                )
            )
        return events

    async def get_announcements(
        self, symbols: list[str], since: datetime | None
    ) -> ProviderResult[list[MaterialEvent]]:
        feed_specs = [(symbol, url) for symbol in symbols for url in self.feeds.get(symbol, [])]
        jobs = [self._feed(symbol, url, since) for symbol, url in feed_specs]
        if not jobs:
            return ProviderResult([], warnings=["Hiçbir resmî IR feed yapılandırılmadı"])
        results = await asyncio.gather(*jobs, return_exceptions=True)
        events: list[MaterialEvent] = []
        warnings: list[str] = []
        successful_scopes: list[str] = []
        for (symbol, url), result in zip(feed_specs, results, strict=True):
            scope = f"ir:{symbol}:{hashlib.sha256(url.encode()).hexdigest()[:12]}"
            if isinstance(result, BaseException):
                warnings.append(
                    f"{symbol}/{scope.rsplit(':', 1)[-1]}: {type(result).__name__} nedeniyle feed alınamadı"
                )
            else:
                events.extend(result)
                successful_scopes.append(scope)
        return ProviderResult(events, warnings=warnings, successful_scopes=successful_scopes)
