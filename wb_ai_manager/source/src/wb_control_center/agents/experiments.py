from __future__ import annotations

from .base import BaseAgent
from ..models import AgentResult


class ExperimentsAgent(BaseAgent):
    name = "experiments"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        # Experiment orchestration is deliberately stateful and conservative.
        # This agent reads experiments created through /experiments API in future versions.
        # For v0.1 it confirms the slot is active without making writes.
        return out
