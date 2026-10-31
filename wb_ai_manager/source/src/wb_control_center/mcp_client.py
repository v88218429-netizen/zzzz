from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any


class WBMCPError(RuntimeError):
    pass


class WBMCPClient:
    """Persistent MCP client that launches wb-mcp-server as a subprocess.

    MCP imports are intentionally lazy so `wb-control demo` works before any
    external dependency is installed.
    """

    def __init__(self, token: str, data_dir: str, shop_id: str | None = None, *, start_timeout: float = 45.0, call_timeout: float = 90.0):
        self.token = token
        self.data_dir = data_dir
        self.shop_id = shop_id
        self.start_timeout = max(1.0, float(start_timeout))
        self.call_timeout = max(1.0, float(call_timeout))
        self.session: Any = None
        self.tools: set[str] = set()
        self._lifecycle_lock = asyncio.Lock()
        self._owner_task: asyncio.Task[None] | None = None
        self._owner_stop: asyncio.Event | None = None
        self._ready: asyncio.Future[BaseException | None] | None = None

    async def start(self) -> None:
        if not self.token:
            raise WBMCPError("WB_API_TOKEN is empty")

        async with self._lifecycle_lock:
            task = self._owner_task
            ready = self._ready
            if self.session is not None and task is not None and not task.done():
                return
            if task is None or task.done():
                loop = asyncio.get_running_loop()
                ready = loop.create_future()
                stop = asyncio.Event()
                task = asyncio.create_task(
                    self._run_transport(ready, stop),
                    name="wb-mcp-transport-owner",
                )
                self._ready = ready
                self._owner_stop = stop
                self._owner_task = task

        assert ready is not None
        try:
            startup_error = await asyncio.wait_for(
                asyncio.shield(ready), timeout=self.start_timeout
            )
        except asyncio.TimeoutError as exc:
            await self.close()
            raise WBMCPError(
                f"wb-mcp startup timed out after {self.start_timeout:g}s"
            ) from exc
        if startup_error is not None:
            raise WBMCPError(
                f"wb-mcp startup failed: {type(startup_error).__name__}: {startup_error}"
            ) from startup_error

    async def _run_transport(
        self,
        ready: asyncio.Future[BaseException | None],
        stop: asyncio.Event,
    ) -> None:
        """Own the stdio/AnyIO context for its full lifetime in one task."""
        try:
            try:
                from mcp import ClientSession, StdioServerParameters
                from mcp.client.stdio import stdio_client
            except ImportError as exc:
                raise WBMCPError(
                    "MCP package is not installed. Run START_WB_AI_MANAGER.command first."
                ) from exc

            env = {
                "WB_API_TOKEN": self.token,
                "DATA_DIR": self.data_dir,
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
                "WB_MAX_RESPONSE_CHARS": "12000000",
            }
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "wb_control_center.wb_mcp_runner"],
                env=env,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self.session = session
                    self.tools = {tool.name for tool in listed.tools}
                    if not ready.done():
                        ready.set_result(None)
                    await stop.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_result(exc)
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            self.session = None
            self.tools = set()
            if not ready.done():
                ready.set_result(WBMCPError("wb-mcp transport closed before startup completed"))

    async def close(self) -> None:
        async with self._lifecycle_lock:
            task = self._owner_task
            stop = self._owner_stop
            ready = self._ready
            if stop is not None:
                stop.set()
            # A startup timeout must unwind the AnyIO context from its owner task.
            if task is not None and not task.done() and ready is not None and not ready.done():
                task.cancel()
            if task is not None and task is not asyncio.current_task():
                done, _ = await asyncio.wait({task}, timeout=10.0)
                if task not in done:
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if self._owner_task is task:
                self._owner_task = None
                self._owner_stop = None
                self._ready = None
                self.session = None
                self.tools = set()

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        # These two narrow Content API operations are implemented in the local
        # manager because the installed MCP connector does not expose the
        # recover-from-trash endpoints. They use the same seller token and never
        # route credentials through another service.
        if tool in {"wb_cards_trash", "wb_cards_recover"}:
            return await self._call_cards_content_api(tool, dict(arguments or {}))
        if not self.session:
            await self.start()
        session = self.session
        if session is None:
            raise WBMCPError("WB MCP session is not connected")
        if self.tools and tool not in self.tools:
            raise WBMCPError(f"MCP tool not available: {tool}")
        args = dict(arguments or {})
        if self.shop_id and tool not in {"wb_list_shops", "wb_degradations"}:
            supplied = args.get("shop_id")
            if supplied is not None and str(supplied) != str(self.shop_id):
                raise WBMCPError(f"tenant/shop mismatch: configured {self.shop_id}, requested {supplied}")
            args["shop_id"] = self.shop_id
        try:
            result = await asyncio.wait_for(
                session.call_tool(tool, arguments=args), timeout=self.call_timeout
            )
        except asyncio.TimeoutError as exc:
            await self.close()
            raise WBMCPError(
                f"MCP tool {tool} timed out after {self.call_timeout:g}s; connector session was reset"
            ) from exc
        if getattr(result, "isError", False) or getattr(result, "is_error", False):
            raise WBMCPError(f"Tool {tool} returned error: {result}")
        structured = getattr(result, "structured_content", None)
        if structured is not None:
            return structured
        texts: list[str] = []
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text is not None:
                texts.append(text)
        if not texts:
            return {"raw": str(result)}
        try:
            parsed = json.loads(texts[0])
            if len(texts) > 1 and isinstance(parsed, dict):
                parsed = dict(parsed)
                parsed["_mcp_notes"] = texts[1:]
            return parsed
        except Exception:
            return {"text": texts[0], "notes": texts[1:]} if len(texts) > 1 else {"text": texts[0]}

    async def _call_cards_content_api(self, tool: str, arguments: dict[str, Any]) -> Any:
        if not self.token:
            raise WBMCPError("WB_API_TOKEN is empty")
        import httpx

        headers = {"Authorization": self.token, "Content-Type": "application/json"}
        if tool == "wb_cards_recover":
            nm_ids = arguments.get("nm_ids")
            if not isinstance(nm_ids, list) or len(nm_ids) != 1:
                raise WBMCPError("card recovery accepts exactly one WB article per approval")
            try:
                article = int(nm_ids[0])
            except (TypeError, ValueError) as exc:
                raise WBMCPError("nm_ids must contain a positive WB article number") from exc
            if article <= 0:
                raise WBMCPError("nm_ids must contain a positive WB article number")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://content-api.wildberries.ru/content/v2/cards/recover",
                    headers=headers,
                    json={"nmIDs": [article]},
                )
                response.raise_for_status()
                return response.json()

        # WB uses cursor pagination for the trash endpoint. Bound reads so a
        # broken cursor cannot produce an unbounded loop or an oversized response.
        limit = max(1, min(int(arguments.get("limit") or 100), 100))
        cursor: dict[str, Any] = {"limit": limit}
        cards: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        async with httpx.AsyncClient(timeout=30.0) as client:
            for _ in range(50):
                response = await client.post(
                    "https://content-api.wildberries.ru/content/v2/get/cards/trash",
                    headers=headers,
                    json={"settings": {"sort": {"ascending": True}, "cursor": cursor}},
                )
                response.raise_for_status()
                payload = response.json()
                body = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
                rows = body.get("cards") if isinstance(body, dict) else None
                if not isinstance(rows, list):
                    raise WBMCPError("Wildberries trash response has no cards list")
                cards.extend(row for row in rows if isinstance(row, dict))
                next_cursor = body.get("cursor") if isinstance(body.get("cursor"), dict) else {}
                if not next_cursor and isinstance(payload, dict) and isinstance(payload.get("cursor"), dict):
                    next_cursor = payload["cursor"]
                trashed_at = str(next_cursor.get("trashedAt") or "")
                nm_id = str(next_cursor.get("nmID") or next_cursor.get("nmId") or "")
                marker = (trashed_at, nm_id)
                total = next_cursor.get("total")
                if len(rows) < limit or (isinstance(total, int) and total <= len(cards)):
                    return {"cards": cards, "count": len(cards), "complete": True, "source": "wildberries_content_api"}
                if not trashed_at or not nm_id or marker in seen:
                    return {"cards": cards, "count": len(cards), "complete": False, "source": "wildberries_content_api", "note": "pagination cursor missing or repeated"}
                seen.add(marker)
                cursor = {"limit": limit, "trashedAt": trashed_at, "nmID": int(nm_id)}
        return {"cards": cards, "count": len(cards), "complete": False, "source": "wildberries_content_api", "note": "pagination safety limit reached"}
