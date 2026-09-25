from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal

Severity = Literal["info", "warning", "critical"]
Risk = Literal["read", "safe", "medium", "high", "irreversible"]


@dataclass(frozen=True)
class PeriodContext:
    from_date: str
    to_date: str
    key: str
    label: str
    days: int
    requested_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def from_strings(cls, from_date: str, to_date: str) -> "PeriodContext":
        start = date.fromisoformat(from_date)
        end = date.fromisoformat(to_date)
        if end < start:
            start, end = end, start
        if (end - start).days > 90:
            start = end.fromordinal(end.toordinal() - 90)
        return cls(
            from_date=start.isoformat(),
            to_date=end.isoformat(),
            key=f"{start.isoformat()}__{end.isoformat()}",
            label=f"{start.strftime('%d.%m.%Y')}–{end.strftime('%d.%m.%Y')}",
            days=(end - start).days + 1,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_date,
            "to": self.to_date,
            "key": self.key,
            "label": self.label,
            "days": self.days,
            "requested_at": self.requested_at,
        }


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Event:
    agent: str
    severity: Severity
    key: str
    title: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""
    created_at: str = field(default_factory=utcnow_iso)


@dataclass
class ActionProposal:
    agent: str
    tool: str
    arguments: dict[str, Any]
    reason: str
    risk: Risk = "medium"
    status: str = "pending"
    id: int | None = None
    created_at: str = field(default_factory=utcnow_iso)


@dataclass
class AgentResult:
    agent: str
    events: list[Event] = field(default_factory=list)
    actions: list[ActionProposal] = field(default_factory=list)
    snapshots: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class DecisionCard:
    decision_key: str
    scope: str
    entity_id: str
    title: str
    diagnosis: str
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    confidence: Literal["low", "medium", "high"] = "medium"
    recommended_actions: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    follow_up: str = ""
    created_at: str = field(default_factory=utcnow_iso)
