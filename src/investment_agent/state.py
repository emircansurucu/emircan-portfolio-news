from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from investment_agent.models import (
    AIEventAnalysis,
    Cadence,
    MaterialEvent,
    PortfolioSnapshot,
)

AnalysisState = Literal["pending", "completed", "failed", "deferred", "skipped"]


class StateRepository(Protocol):
    def last_success(self, cadence: Cadence) -> datetime | None: ...

    def provider_checkpoint(self, scope: str) -> datetime | None: ...

    def is_processed(self, event: MaterialEvent, cadence: Cadence) -> bool: ...

    def history(self) -> list[dict[str, Any]]: ...

    def unreported_events(
        self, cadence: Cadence, since: datetime | None, earliest: datetime
    ) -> list[MaterialEvent]: ...

    def pending_analysis_events(self) -> list[MaterialEvent]: ...

    def completed_unreported_analyses(
        self, cadence: Cadence
    ) -> list[tuple[MaterialEvent, AIEventAnalysis]]: ...

    def record_discovered(self, events: list[MaterialEvent], at: datetime) -> None: ...

    def analysis_for(self, event: MaterialEvent) -> AIEventAnalysis | None: ...

    def analysis_state(self, event: MaterialEvent) -> str: ...

    def mark_analysis(
        self,
        event: MaterialEvent,
        status: AnalysisState,
        at: datetime,
        *,
        analysis: AIEventAnalysis | None = None,
        reason: str | None = None,
    ) -> None: ...

    def commit_success(
        self,
        cadence: Cadence,
        at: datetime,
        report_id: str,
        snapshot: PortfolioSnapshot | None,
        events: list[MaterialEvent],
        analysis_events: list[MaterialEvent],
        provider_checkpoints: dict[str, datetime],
    ) -> None: ...

    def enqueue_delivery(
        self,
        report_id: str,
        text: str,
        event_ids: list[str],
        analysis_event_ids: list[str],
        at: datetime,
    ) -> None: ...

    def pending_deliveries(self, at: datetime) -> list[dict[str, Any]]: ...

    def mark_delivery(
        self, report_id: str, *, success: bool, at: datetime, error: str | None = None
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
        self.backup_path = data_dir / "state.json.bak"
        self.history_path = data_dir / "portfolio_history.jsonl"
        self.events_path = data_dir / "processed_events.jsonl"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "version": 3,
            "last_success": {},
            "provider_checkpoints": {},
            "processed_event_ids": [],
            "reported_event_ids": {},
            "event_lifecycle": {},
            "outbox": [],
            "reports": [],
        }

    @classmethod
    def _migrate(cls, state: dict[str, Any]) -> dict[str, Any]:
        defaults = cls._default_state()
        for key, value in defaults.items():
            state.setdefault(key, value)
        lifecycle = state["event_lifecycle"]
        for entry in lifecycle.values():
            ai = entry.setdefault(
                "ai",
                {"status": "pending", "attempts": 0, "reported": {}, "deliveries": []},
            )
            ai.setdefault("reported", {})
            ai.setdefault("deliveries", [])
            if ai.get("status") == "completed" and ai.get("updated_at"):
                ai_updated = datetime.fromisoformat(ai["updated_at"])
                for cadence, factual_reported_at in entry.get("reported", {}).items():
                    if cadence in ai["reported"]:
                        continue
                    factual_time = datetime.fromisoformat(
                        factual_reported_at["reported_at"]
                        if isinstance(factual_reported_at, dict)
                        else factual_reported_at
                    )
                    if ai_updated <= factual_time:
                        ai["reported"][cadence] = {
                            "report_id": "legacy-inferred",
                            "reported_at": factual_time.isoformat(),
                            "migration_inferred": True,
                        }
        for item in state["outbox"]:
            item.setdefault("analysis_event_ids", [])
        state["version"] = 3
        return state

    def _state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._default_state()
        try:
            with self.state_path.open(encoding="utf-8") as handle:
                return self._migrate(json.load(handle))
        except (json.JSONDecodeError, OSError) as primary_error:
            if self.backup_path.exists():
                try:
                    with self.backup_path.open(encoding="utf-8") as handle:
                        return self._migrate(json.load(handle))
                except (json.JSONDecodeError, OSError):
                    pass
            raise RuntimeError(
                "State dosyası ve yedeği okunamadı; otomatik sıfırlama yapılmadı"
            ) from primary_error

    def last_success(self, cadence: Cadence) -> datetime | None:
        raw = self._state()["last_success"].get(cadence.value)
        return datetime.fromisoformat(raw) if raw else None

    def provider_checkpoint(self, scope: str) -> datetime | None:
        raw = self._state()["provider_checkpoints"].get(scope)
        return datetime.fromisoformat(raw) if raw else None

    def is_processed(self, event: MaterialEvent, cadence: Cadence) -> bool:
        reported = self._state()["reported_event_ids"].get(cadence.value, [])
        return event_fingerprint(event) in set(reported)

    def history(self) -> list[dict[str, Any]]:
        if not self.history_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.history_path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"Portfolio history bozuk JSONL satırı içeriyor: {line_number}"
                    ) from exc
        return rows

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
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

    def _write_state(self, payload: dict[str, Any]) -> None:
        if self.state_path.exists():
            try:
                with self.state_path.open(encoding="utf-8") as handle:
                    previous = json.load(handle)
                self._write_json(self.backup_path, previous)
            except (json.JSONDecodeError, OSError):
                # Preserve the last known-good backup when the primary is corrupt.
                pass
        self._write_json(self.state_path, payload)

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

    def _event_rows(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        with self.events_path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def unreported_events(
        self, cadence: Cadence, since: datetime | None, earliest: datetime
    ) -> list[MaterialEvent]:
        state = self._state()
        reported = set(state["reported_event_ids"].get(cadence.value, []))
        events: list[MaterialEvent] = []
        for row in self._event_rows():
            if row["fingerprint"] in reported:
                continue
            discovered_at = datetime.fromisoformat(row["discovered_at"])
            event = MaterialEvent.model_validate(row["event"])
            if (since and discovered_at > since) or (
                since is None and event.occurred_at >= earliest
            ):
                events.append(event)
        return events

    def pending_analysis_events(self) -> list[MaterialEvent]:
        state = self._state()
        lifecycle = state["event_lifecycle"]
        events: list[MaterialEvent] = []
        for row in self._event_rows():
            status = lifecycle.get(row["fingerprint"], {}).get("ai", {}).get("status", "pending")
            if status in {"pending", "failed", "deferred"}:
                events.append(MaterialEvent.model_validate(row["event"]))
        return events

    def completed_unreported_analyses(
        self, cadence: Cadence
    ) -> list[tuple[MaterialEvent, AIEventAnalysis]]:
        state = self._state()
        lifecycle = state["event_lifecycle"]
        completed: list[tuple[MaterialEvent, AIEventAnalysis]] = []
        for row in self._event_rows():
            ai = lifecycle.get(row["fingerprint"], {}).get("ai", {})
            if (
                ai.get("status") == "completed"
                and ai.get("analysis")
                and cadence.value not in ai.get("reported", {})
            ):
                completed.append(
                    (
                        MaterialEvent.model_validate(row["event"]),
                        AIEventAnalysis.model_validate(ai["analysis"]),
                    )
                )
        return completed

    def record_discovered(self, events: list[MaterialEvent], at: datetime) -> None:
        if not events:
            return
        state = self._state()
        lifecycle = state["event_lifecycle"]
        rows = self._event_rows()
        row_by_id = {row["fingerprint"]: row for row in rows}
        for event in events:
            fingerprint = event_fingerprint(event)
            entry = lifecycle.setdefault(
                fingerprint,
                {
                    "event_id": event.event_id,
                    "discovered_at": at.isoformat(),
                    "reported": {},
                    "ai": {
                        "status": "pending",
                        "attempts": 0,
                        "reported": {},
                        "deliveries": [],
                    },
                    "deliveries": [],
                },
            )
            entry["last_seen_at"] = at.isoformat()
            row_by_id[fingerprint] = {
                "fingerprint": fingerprint,
                "event": event.model_dump(mode="json"),
                "discovered_at": entry["discovered_at"],
                "last_seen_at": at.isoformat(),
            }
        state["processed_event_ids"] = sorted(row_by_id)
        self._atomic_jsonl(self.events_path, list(row_by_id.values()))
        self._write_state(state)

    def analysis_for(self, event: MaterialEvent) -> AIEventAnalysis | None:
        entry = self._state()["event_lifecycle"].get(event_fingerprint(event), {})
        ai = entry.get("ai", {})
        if ai.get("status") != "completed" or not ai.get("analysis"):
            return None
        return AIEventAnalysis.model_validate(ai["analysis"])

    def analysis_state(self, event: MaterialEvent) -> str:
        entry = self._state()["event_lifecycle"].get(event_fingerprint(event), {})
        return str(entry.get("ai", {}).get("status", "pending"))

    def mark_analysis(
        self,
        event: MaterialEvent,
        status: AnalysisState,
        at: datetime,
        *,
        analysis: AIEventAnalysis | None = None,
        reason: str | None = None,
    ) -> None:
        state = self._state()
        fingerprint = event_fingerprint(event)
        entry = state["event_lifecycle"].setdefault(
            fingerprint,
            {
                "event_id": event.event_id,
                "discovered_at": at.isoformat(),
                "reported": {},
                "ai": {
                    "status": "pending",
                    "attempts": 0,
                    "reported": {},
                    "deliveries": [],
                },
                "deliveries": [],
            },
        )
        prior = entry.get("ai", {})
        attempts = int(prior.get("attempts", 0)) + (1 if status in {"completed", "failed"} else 0)
        ai_state: dict[str, Any] = {
            "status": status,
            "attempts": attempts,
            "updated_at": at.isoformat(),
            "reported": prior.get("reported", {}),
            "deliveries": prior.get("deliveries", []),
        }
        if analysis is not None:
            ai_state["analysis"] = analysis.model_dump(mode="json")
        if reason:
            ai_state["reason"] = reason
        entry["ai"] = ai_state
        self._write_state(state)

    def commit_success(
        self,
        cadence: Cadence,
        at: datetime,
        report_id: str,
        snapshot: PortfolioSnapshot | None,
        events: list[MaterialEvent],
        analysis_events: list[MaterialEvent] | None = None,
        provider_checkpoints: dict[str, datetime] | None = None,
    ) -> None:
        state = self._state()
        identifiers = set(state["processed_event_ids"])
        identifiers.update(event_fingerprint(event) for event in events)
        state["processed_event_ids"] = sorted(identifiers)
        reported = set(state["reported_event_ids"].get(cadence.value, []))
        for event in events:
            fingerprint = event_fingerprint(event)
            reported.add(fingerprint)
            entry = state["event_lifecycle"].setdefault(
                fingerprint,
                {
                    "event_id": event.event_id,
                    "discovered_at": at.isoformat(),
                    "reported": {},
                    "ai": {
                        "status": "pending",
                        "attempts": 0,
                        "reported": {},
                        "deliveries": [],
                    },
                    "deliveries": [],
                },
            )
            entry["reported"][cadence.value] = at.isoformat()
        state["reported_event_ids"][cadence.value] = sorted(reported)
        for event in analysis_events or []:
            fingerprint = event_fingerprint(event)
            entry = state["event_lifecycle"].get(fingerprint)
            if entry is None or entry.get("ai", {}).get("status") != "completed":
                raise RuntimeError(
                    f"Tamamlanmamış AI analizi raporlandı olarak işaretlenemez: {event.event_id}"
                )
            entry["ai"].setdefault("reported", {})[cadence.value] = {
                "report_id": report_id,
                "reported_at": at.isoformat(),
            }
        state["provider_checkpoints"].update(
            {
                scope: timestamp.isoformat()
                for scope, timestamp in (provider_checkpoints or {}).items()
            }
        )
        reports = state["reports"]
        reports.append(
            {"report_id": report_id, "cadence": cadence.value, "created_at": at.isoformat()}
        )
        state["reports"] = reports[-1000:]
        state["last_success"][cadence.value] = at.isoformat()

        history = self.history()
        if snapshot is not None:
            session = (snapshot.market_session or snapshot.as_of.date()).isoformat()
            record = {
                "market_session": session,
                "as_of": snapshot.as_of.isoformat(),
                "total_value_usd": snapshot.total_value_usd,
                "total_value_try": snapshot.total_value_try,
                "usdtry": snapshot.usdtry,
                "market_prices_usd": snapshot.market_prices_usd,
                "position_values_usd": {
                    position.symbol: position.value_usd for position in snapshot.positions
                },
                "position_quantities": {
                    position.symbol: position.quantity for position in snapshot.positions
                },
                "report_id": report_id,
            }
            history = [row for row in history if row.get("market_session") != session]
            history.append(record)
            history.sort(key=lambda row: str(row.get("market_session") or row["as_of"]))

        self._atomic_jsonl(self.history_path, history)
        event_rows = self._event_rows()
        event_row_by_id = {row["fingerprint"]: row for row in event_rows}
        for event in events:
            fingerprint = event_fingerprint(event)
            event_row_by_id.setdefault(
                fingerprint,
                {
                    "fingerprint": fingerprint,
                    "event": event.model_dump(mode="json"),
                    "discovered_at": at.isoformat(),
                    "last_seen_at": at.isoformat(),
                },
            )
        self._atomic_jsonl(self.events_path, list(event_row_by_id.values()))
        self._write_state(state)

    def enqueue_delivery(
        self,
        report_id: str,
        text: str,
        event_ids: list[str],
        analysis_event_ids: list[str] | None,
        at: datetime,
    ) -> None:
        state = self._state()
        if any(item["report_id"] == report_id for item in state["outbox"]):
            return
        state["outbox"].append(
            {
                "report_id": report_id,
                "text": text,
                "event_ids": event_ids,
                "analysis_event_ids": analysis_event_ids or [],
                "status": "pending",
                "attempts": 0,
                "created_at": at.isoformat(),
                "next_attempt_at": at.isoformat(),
            }
        )
        self._write_state(state)

    def pending_deliveries(self, at: datetime) -> list[dict[str, Any]]:
        return [
            item
            for item in self._state()["outbox"]
            if item["status"] != "delivered"
            and datetime.fromisoformat(item["next_attempt_at"]) <= at
        ]

    def mark_delivery(
        self, report_id: str, *, success: bool, at: datetime, error: str | None = None
    ) -> None:
        state = self._state()
        item = next(entry for entry in state["outbox"] if entry["report_id"] == report_id)
        item["attempts"] = int(item.get("attempts", 0)) + 1
        if success:
            item["status"] = "delivered"
            item["delivered_at"] = at.isoformat()
            for fingerprint in item.get("event_ids", []):
                lifecycle = state["event_lifecycle"].get(fingerprint)
                if lifecycle is not None:
                    lifecycle.setdefault("deliveries", []).append(
                        {"report_id": report_id, "delivered_at": at.isoformat()}
                    )
            for fingerprint in item.get("analysis_event_ids", []):
                lifecycle = state["event_lifecycle"].get(fingerprint)
                if lifecycle is not None:
                    lifecycle.setdefault("ai", {}).setdefault("deliveries", []).append(
                        {"report_id": report_id, "delivered_at": at.isoformat()}
                    )
        else:
            item["status"] = "failed"
            item["last_error"] = (error or "delivery failed")[:300]
            delay_minutes = min(2 ** min(item["attempts"], 10), 24 * 60)
            item["next_attempt_at"] = (at + timedelta(minutes=delay_minutes)).isoformat()
        self._write_state(state)
