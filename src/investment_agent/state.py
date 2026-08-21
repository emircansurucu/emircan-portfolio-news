from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from investment_agent.models import Cadence, MaterialEvent, PortfolioSnapshot


class StateRepository(Protocol):
    def last_success(self, cadence: Cadence) -> datetime | None: ...

    def is_processed(self, event: MaterialEvent, cadence: Cadence) -> bool: ...

    def history(self) -> list[dict[str, Any]]: ...

    def commit_success(
        self,
        cadence: Cadence,
        at: datetime,
        report_id: str,
        snapshot: PortfolioSnapshot | None,
        events: list[MaterialEvent],
    ) -> None: ...


def event_fingerprint(event: MaterialEvent) -> str:
    if event.accession_number:
        return f"sec:{event.accession_number}"
    normalized = "|".join(
        [event.symbol.upper(), event.title.strip().lower(), str(event.source.url).rstrip("/")]
    )
    return hashlib.sha256(normalized.encode()).hexdigest()


class JsonStateRepository:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.state_path = data_dir / "state.json"
        self.history_path = data_dir / "portfolio_history.jsonl"
        self.events_path = data_dir / "processed_events.jsonl"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "version": 1,
                "last_success": {},
                "processed_event_ids": [],
                "reported_event_ids": {},
                "reports": [],
            }
        with self.state_path.open(encoding="utf-8") as handle:
            return json.load(handle)

    def last_success(self, cadence: Cadence) -> datetime | None:
        raw = self._state().get("last_success", {}).get(cadence.value)
        return datetime.fromisoformat(raw) if raw else None

    def is_processed(self, event: MaterialEvent, cadence: Cadence) -> bool:
        reported = self._state().get("reported_event_ids", {}).get(cadence.value, [])
        return event_fingerprint(event) in set(reported)

    def history(self) -> list[dict[str, Any]]:
        if not self.history_path.exists():
            return []
        with self.history_path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    @staticmethod
    def _atomic_json(path: Path, payload: Any) -> None:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise

    @staticmethod
    def _atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise

    def commit_success(
        self,
        cadence: Cadence,
        at: datetime,
        report_id: str,
        snapshot: PortfolioSnapshot | None,
        events: list[MaterialEvent],
    ) -> None:
        state = self._state()
        identifiers = set(state.get("processed_event_ids", []))
        identifiers.update(event_fingerprint(event) for event in events)
        state["processed_event_ids"] = sorted(identifiers)
        reported_by_cadence = state.setdefault("reported_event_ids", {})
        reported = set(reported_by_cadence.get(cadence.value, []))
        reported.update(event_fingerprint(event) for event in events)
        reported_by_cadence[cadence.value] = sorted(reported)
        reports = state.setdefault("reports", [])
        reports.append(
            {"report_id": report_id, "cadence": cadence.value, "created_at": at.isoformat()}
        )
        state["reports"] = reports[-1000:]
        state.setdefault("last_success", {})[cadence.value] = at.isoformat()

        history = self.history()
        if snapshot is not None:
            history.append(
                {
                    "as_of": snapshot.as_of.isoformat(),
                    "total_value_usd": snapshot.total_value_usd,
                    "total_value_try": snapshot.total_value_try,
                    "usdtry": snapshot.usdtry,
                    "market_prices_usd": snapshot.market_prices_usd,
                    "position_values_usd": {
                        position.symbol: position.value_usd for position in snapshot.positions
                    },
                    "report_id": report_id,
                }
            )
        existing_events: list[dict[str, Any]] = []
        if self.events_path.exists():
            with self.events_path.open(encoding="utf-8") as handle:
                existing_events = [json.loads(line) for line in handle if line.strip()]
        existing_ids = {row["fingerprint"] for row in existing_events}
        for event in events:
            fingerprint = event_fingerprint(event)
            if fingerprint not in existing_ids:
                existing_events.append(
                    {
                        "fingerprint": fingerprint,
                        "event_id": event.event_id,
                        "symbol": event.symbol,
                        "title": event.title,
                        "url": str(event.source.url),
                        "occurred_at": event.occurred_at.isoformat(),
                    }
                )

        # Audit files are replaced atomically; state.json is the authoritative commit marker.
        self._atomic_jsonl(self.history_path, history)
        self._atomic_jsonl(self.events_path, existing_events)
        self._atomic_json(self.state_path, state)
