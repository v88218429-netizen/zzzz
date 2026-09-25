from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import PolicyConfig, Settings
from ..db import Database
from ..llm import LLMClient
from ..mcp_client import WBMCPClient
from ..worker_source import WorkerSource
from ..models import AgentResult, Event, PeriodContext
from ..metrics import fingerprint

log = logging.getLogger(__name__)

_analysis_period_var: ContextVar[PeriodContext | None] = ContextVar("wb_analysis_period", default=None)

def set_analysis_period(period: PeriodContext | None):
    return _analysis_period_var.set(period)

def reset_analysis_period(token) -> None:
    _analysis_period_var.reset(token)

def current_analysis_period() -> PeriodContext | None:
    return _analysis_period_var.get()


@dataclass
class AgentContext:
    settings: Settings
    policy: PolicyConfig
    db: Database
    wb: WBMCPClient
    llm: LLMClient
    worker: WorkerSource | None = None


class BaseAgent:
    name = "base"

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx

    async def run(self) -> AgentResult:
        raise NotImplementedError

    async def call(self, tool: str, **kwargs: Any) -> Any:
        return await self.ctx.wb.call(tool, kwargs)

    def event(self, severity: str, key: str, title: str, message: str, payload: dict[str, Any] | None = None) -> Event:
        payload = payload or {}
        return Event(
            agent=self.name,
            severity=severity,  # type: ignore[arg-type]
            key=key,
            title=title,
            message=message,
            payload=payload,
            fingerprint=fingerprint(self.name, key, payload),
        )

    def analysis_period(self) -> PeriodContext | None:
        return current_analysis_period()

    def dates(self, days: int = 1) -> tuple[str, str]:
        period = self.analysis_period()
        if period is not None:
            return period.from_date, period.to_date
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=max(0, days - 1))
        return start.isoformat(), today.isoformat()

    def period_meta(self, scope: str = "period") -> dict[str, Any]:
        period = self.analysis_period()
        if period is None:
            return {"mode": "operational_current", "scope": scope}
        return {"mode": "period_audit", "scope": scope, "period": period.to_dict()}
