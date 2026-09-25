from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import PolicyConfig, Settings
from ..db import Database
from ..llm import LLMClient
from ..mcp_client import WBMCPClient
from ..models import AgentResult, Event
from ..metrics import fingerprint

log = logging.getLogger(__name__)


@dataclass
class AgentContext:
    settings: Settings
    policy: PolicyConfig
    db: Database
    wb: WBMCPClient
    llm: LLMClient


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

    @staticmethod
    def dates(days: int = 1) -> tuple[str, str]:
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=max(0, days - 1))
        return start.isoformat(), today.isoformat()
