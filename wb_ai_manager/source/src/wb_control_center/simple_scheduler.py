from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Awaitable, Callable


class SimpleAsyncScheduler:
    """Tiny stdlib-only scheduler used when APScheduler is unavailable.

    It supports exactly the two patterns this project needs: interval jobs in minutes
    and daily cron jobs at hour/minute. Jobs are async callables. This makes the
    product one-click runnable even before optional third-party scheduler packages
    are installed.
    """

    def __init__(self, timezone: str = "UTC"):
        try:
            self.tz = ZoneInfo(timezone)
        except Exception:
            self.tz = ZoneInfo("UTC")
        self.jobs: list[dict[str, Any]] = []
        self.tasks: list[asyncio.Task] = []
        self.running = False

    def add_job(self, func: Callable[..., Awaitable[Any]], trigger: str, **kwargs: Any) -> None:
        self.jobs.append({"func": func, "trigger": trigger, **kwargs})

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        for job in self.jobs:
            if job["trigger"] == "interval":
                self.tasks.append(asyncio.create_task(self._interval_loop(job)))
            elif job["trigger"] == "cron":
                self.tasks.append(asyncio.create_task(self._cron_loop(job)))

    def shutdown(self, wait: bool = False) -> None:
        self.running = False
        for task in self.tasks:
            task.cancel()
        self.tasks.clear()

    async def _call(self, job: dict[str, Any]) -> None:
        try:
            await job["func"](*(job.get("args") or []))
        except asyncio.CancelledError:
            raise
        except Exception:
            # The ControlCenter already records per-agent failures; a scheduler loop
            # must stay alive even when one run fails.
            pass

    async def _interval_loop(self, job: dict[str, Any]) -> None:
        seconds = max(1.0, float(job.get("minutes", 1)) * 60.0)
        try:
            while self.running:
                await asyncio.sleep(seconds)
                if self.running:
                    await self._call(job)
        except asyncio.CancelledError:
            return

    async def _cron_loop(self, job: dict[str, Any]) -> None:
        hour = int(job.get("hour", 0))
        minute = int(job.get("minute", 0))
        try:
            while self.running:
                now = datetime.now(self.tz)
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                await asyncio.sleep(max(1.0, (target - now).total_seconds()))
                if self.running:
                    await self._call(job)
        except asyncio.CancelledError:
            return
