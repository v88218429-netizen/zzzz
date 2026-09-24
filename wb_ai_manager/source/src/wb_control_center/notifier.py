from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import httpx

from .config import Settings
from .models import Event

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, settings: Settings):
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self._offset = 0
        self._stop = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    async def send(self, text: str) -> None:
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(url, json={"chat_id": self.chat_id, "text": text[:4000], "disable_web_page_preview": True})
            r.raise_for_status()

    async def send_event(self, event: Event) -> None:
        icon = {"critical": "🔴", "warning": "🟠", "info": "🔵"}.get(event.severity, "•")
        await self.send(f"{icon} {event.title}\n\n{event.message}\n\nАгент: {event.agent}")

    async def send_action(self, action_id: int, tool: str, reason: str, risk: str) -> None:
        await self.send(
            f"🧠 Предложено действие #{action_id}\n{tool}\nРиск: {risk}\n\n{reason}\n\n"
            f"Подтвердить: /approve {action_id}\nОтклонить: /reject {action_id}"
        )

    async def poll_commands(
        self,
        on_approve: Callable[[int], Awaitable[str]],
        on_reject: Callable[[int], Awaitable[str]],
        on_status: Callable[[], Awaitable[str]],
    ) -> None:
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        while not self._stop.is_set():
            try:
                async with httpx.AsyncClient(timeout=35) as client:
                    r = await client.get(url, params={"timeout": 25, "offset": self._offset})
                    r.raise_for_status()
                    data = r.json()
                for upd in data.get("result", []):
                    self._offset = max(self._offset, int(upd["update_id"]) + 1)
                    msg = upd.get("message") or upd.get("edited_message") or {}
                    if str((msg.get("chat") or {}).get("id")) != str(self.chat_id):
                        continue
                    text = (msg.get("text") or "").strip()
                    if text.startswith("/approve"):
                        parts = text.split()
                        if len(parts) == 2 and parts[1].isdigit():
                            await self.send(await on_approve(int(parts[1])))
                    elif text.startswith("/reject"):
                        parts = text.split()
                        if len(parts) == 2 and parts[1].isdigit():
                            await self.send(await on_reject(int(parts[1])))
                    elif text.startswith("/status"):
                        await self.send(await on_status())
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Telegram polling error: %s", e)
                await asyncio.sleep(5)

    def stop(self) -> None:
        self._stop.set()
