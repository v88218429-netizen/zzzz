from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from curl_cffi import requests as curl_requests
from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

APP_VERSION = "1.0.0"
MAX_EVENTS = 20000
CHECK_LOOP_SECONDS = 3
SOURCE_NAME = "Ozon storefront JSON"

COMPOSER_ENDPOINTS = [
    "https://api.ozon.ru/composer-api.bx/page/json/v2",
    "https://www.ozon.ru/api/composer-api.bx/page/json/v2",
    "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2",
]

app = FastAPI(title="Ozon Live Radar", version=APP_VERSION)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def env(name: str, *fallbacks: str) -> str:
    for key in (name, *fallbacks):
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


RADAR_SECRET = env("RADAR_SECRET")
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", "BOT_TOKEN")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID", "OWNER_CHAT_ID")
OZON_PROXY = env("OZON_PROXY")
RADAR_TASKS_JSON = env("RADAR_TASKS_JSON")


class RadarTaskIn(BaseModel):
    article: str
    sku: str
    query: str
    interval_min: int = Field(default=1, ge=1, le=60)
    drop_threshold: int = Field(default=3, ge=1, le=1000)
    top_boundary: int = Field(default=10, ge=1, le=1000)
    max_position: int = Field(default=100, ge=10, le=500)
    baseline_position: int | None = Field(default=None, ge=1, le=10000)
    enabled: bool = True


class ConfigIn(BaseModel):
    tasks: list[RadarTaskIn]


@dataclass
class TaskState:
    task: RadarTaskIn
    last_position: int | None = None
    last_checked_ts: float = 0.0
    last_checked_iso: str = ""
    next_due_ts: float = 0.0
    alert_active: bool = False
    last_alert_iso: str = ""
    status: str = "WAITING"
    source: str = SOURCE_NAME
    endpoint: str = ""
    response_ms: int = 0
    http_status: int = 0
    last_error: str = ""


@dataclass
class Runtime:
    tasks: dict[str, TaskState] = field(default_factory=dict)
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_EVENTS))
    seq: int = 0
    last_cycle_iso: str = ""
    last_success_iso: str = ""
    last_error: str = ""


runtime = Runtime()


def task_key(task: RadarTaskIn) -> str:
    raw = f"{task.article}|{task.sku}|{task.query}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def require_auth(authorization: str | None) -> None:
    if not RADAR_SECRET:
        raise HTTPException(status_code=503, detail="RADAR_SECRET is not configured")
    expected = "Bearer " + RADAR_SECRET
    if authorization != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


def extract_skus_from_widget_states(data: dict[str, Any]) -> list[str]:
    states = data.get("widgetStates")
    if not isinstance(states, dict):
        return []

    found: list[str] = []
    seen: set[str] = set()

    for name, raw in states.items():
        if "tileGridDesktop" not in name and "searchResultsV2" not in name:
            continue
        try:
            state = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        if not isinstance(state, dict):
            continue
        items = state.get("items")
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            sku = item.get("sku") or item.get("skuId") or item.get("id")
            sku_text = str(sku) if sku is not None else ""
            if not sku_text.isdigit():
                blob = json.dumps(item, ensure_ascii=False)
                m = re.search(r"/product/[a-zA-Z0-9\-_]+-(\d{6,})/", blob)
                sku_text = m.group(1) if m else ""
            if sku_text and sku_text not in seen:
                seen.add(sku_text)
                found.append(sku_text)

    return found


class OzonClient:
    def __init__(self) -> None:
        self.session = curl_requests.Session(impersonate="chrome124")
        self.headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "referer": "https://www.ozon.ru/",
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
            "sec-ch-ua": '"Chromium";v="140", "Google Chrome";v="140", "Not=A?Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
        }

    def _request_page(self, path: str) -> tuple[dict[str, Any], str, int, int]:
        last_error = ""
        proxies = {"http": OZON_PROXY, "https": OZON_PROXY} if OZON_PROXY else None

        for endpoint in COMPOSER_ENDPOINTS:
            started = time.perf_counter()
            try:
                resp = self.session.get(
                    endpoint,
                    params={"url": path},
                    headers=self.headers,
                    timeout=25,
                    allow_redirects=False,
                    proxies=proxies,
                )
            except Exception as exc:
                last_error = f"{endpoint}: transport {type(exc).__name__}: {exc}"
                continue

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            status = int(resp.status_code)

            if status in (307, 401, 403, 429):
                last_error = f"{endpoint}: HTTP {status}"
                continue
            if status >= 500:
                last_error = f"{endpoint}: HTTP {status}"
                continue
            if status != 200:
                last_error = f"{endpoint}: HTTP {status}"
                continue

            try:
                data = resp.json()
            except Exception:
                last_error = f"{endpoint}: invalid JSON"
                continue

            if not isinstance(data, dict):
                last_error = f"{endpoint}: non-object JSON"
                continue

            return data, endpoint, status, elapsed_ms

        raise RuntimeError(last_error or "all Ozon endpoints failed")

    def position(self, query: str, sku: str, max_position: int) -> dict[str, Any]:
        target = str(sku)
        seen: list[str] = []
        seen_set: set[str] = set()
        pages = max(1, min(10, (max_position + 35) // 36))
        total_ms = 0
        last_endpoint = ""
        last_http = 0

        for page in range(1, pages + 1):
            encoded_query = urllib.parse.quote(query, safe="")
            path = f"/search/?text={encoded_query}"
            if page > 1:
                path += f"&page={page}"

            data, endpoint, http_status, elapsed_ms = self._request_page(path)
            total_ms += elapsed_ms
            last_endpoint = endpoint
            last_http = http_status
            page_skus = extract_skus_from_widget_states(data)

            if not page_skus:
                raise RuntimeError(f"Ozon JSON has no search tiles on page {page}")

            for item_sku in page_skus:
                if item_sku in seen_set:
                    continue
                seen_set.add(item_sku)
                seen.append(item_sku)
                if item_sku == target:
                    return {
                        "position": len(seen),
                        "status": "OK",
                        "endpoint": endpoint,
                        "http_status": http_status,
                        "response_ms": total_ms,
                        "checked_depth": len(seen),
                    }
                if len(seen) >= max_position:
                    break

            if len(seen) >= max_position:
                break

        return {
            "position": None,
            "status": f"NOT_FOUND_TOP_{max_position}",
            "endpoint": last_endpoint,
            "http_status": last_http,
            "response_ms": total_ms,
            "checked_depth": len(seen),
        }


ozon = OzonClient()


def add_event(payload: dict[str, Any]) -> dict[str, Any]:
    runtime.seq += 1
    event = {"seq": runtime.seq, **payload}
    runtime.events.append(event)
    return event


def telegram_send(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    body = urllib.parse.urlencode(
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data=body,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        response.read()


def position_text(position: int | None, max_position: int) -> str:
    return str(position) if position is not None else f">{max_position}"


def make_alert(task: RadarTaskIn, prev: int, current: int | None, event_type: str) -> str:
    current_eff = current if current is not None else task.max_position + 1
    current_txt = position_text(current, task.max_position)
    drop = current_eff - prev

    if event_type == "RECOVERY":
        title = "✅ <b>Ozon · позиция восстановилась</b>"
    elif event_type == "OUT_TOP":
        title = "🔴 <b>Ozon · вышли из контролируемого TOP</b>"
    else:
        title = "🔴 <b>Ozon · падение позиции</b>"

    return (
        f"{title}\n\n"
        f"Артикул: <b>{task.article}</b>\n"
        f"Запрос: <b>{task.query}</b>\n"
        f"Позиция: <b>{prev} → {current_txt}</b>\n"
        f"Изменение: <b>{'+' if drop > 0 else ''}{drop}</b> по выдаче\n"
        f"Контроль: TOP-{task.top_boundary} · порог {task.drop_threshold} поз.\n"
        f"Проверено: {now_iso()}"
    )


async def check_one(key: str, state: TaskState) -> None:
    task = state.task
    previous = state.last_position
    checked_iso = now_iso()

    try:
        result = await asyncio.to_thread(ozon.position, task.query, task.sku, task.max_position)
        position = result["position"]
        current_eff = position if position is not None else task.max_position + 1
        previous_eff = previous

        state.last_checked_ts = time.time()
        state.last_checked_iso = checked_iso
        state.next_due_ts = state.last_checked_ts + task.interval_min * 60
        state.status = result["status"]
        state.source = SOURCE_NAME
        state.endpoint = result["endpoint"]
        state.response_ms = result["response_ms"]
        state.http_status = result["http_status"]
        state.last_error = ""
        runtime.last_success_iso = checked_iso
        runtime.last_error = ""

        alert_type = ""
        alert_message = ""
        delta = None if previous_eff is None else previous_eff - current_eff

        if previous_eff is not None:
            drop = current_eff - previous_eff
            crossed = previous_eff <= task.top_boundary and current_eff > task.top_boundary
            material_drop = drop >= task.drop_threshold

            if crossed or material_drop:
                alert_type = "OUT_TOP" if crossed else "DROP"
                alert_message = make_alert(task, previous_eff, position, alert_type)
                state.alert_active = True
                state.last_alert_iso = checked_iso
            elif state.alert_active and current_eff <= task.top_boundary:
                alert_type = "RECOVERY"
                alert_message = make_alert(task, previous_eff, position, alert_type)
                state.alert_active = False
                state.last_alert_iso = checked_iso

        add_event(
            {
                "kind": "CHECK",
                "checked_at": checked_iso,
                "key": key,
                "article": task.article,
                "sku": task.sku,
                "query": task.query,
                "position": position,
                "position_text": position_text(position, task.max_position),
                "previous": previous_eff,
                "delta": delta,
                "status": state.status,
                "source": state.source,
                "endpoint": state.endpoint,
                "http_status": state.http_status,
                "response_ms": state.response_ms,
                "alert_type": alert_type,
                "alert_message": alert_message,
            }
        )

        if alert_message:
            try:
                await asyncio.to_thread(telegram_send, alert_message)
            except Exception as exc:
                add_event(
                    {
                        "kind": "TELEGRAM_ERROR",
                        "checked_at": checked_iso,
                        "key": key,
                        "article": task.article,
                        "sku": task.sku,
                        "query": task.query,
                        "status": f"Telegram error: {type(exc).__name__}: {exc}",
                    }
                )

        state.last_position = current_eff

    except Exception as exc:
        state.last_checked_ts = time.time()
        state.last_checked_iso = checked_iso
        state.next_due_ts = state.last_checked_ts + max(60, task.interval_min * 60)
        state.status = "SOURCE_ERROR"
        state.last_error = f"{type(exc).__name__}: {exc}"
        runtime.last_error = state.last_error

        add_event(
            {
                "kind": "ERROR",
                "checked_at": checked_iso,
                "key": key,
                "article": task.article,
                "sku": task.sku,
                "query": task.query,
                "position": None,
                "previous": previous,
                "delta": None,
                "status": state.status,
                "source": SOURCE_NAME,
                "error": state.last_error,
                "alert_type": "",
                "alert_message": "",
            }
        )


async def scheduler() -> None:
    while True:
        runtime.last_cycle_iso = now_iso()
        now = time.time()
        due = [
            (key, state)
            for key, state in list(runtime.tasks.items())
            if state.task.enabled and state.next_due_ts <= now
        ]

        # Ozon is deliberately checked sequentially. Parallel storefront search
        # is much more likely to trigger rate/anti-bot protection.
        for key, state in due:
            await check_one(key, state)
            await asyncio.sleep(0.6)

        await asyncio.sleep(CHECK_LOOP_SECONDS)


def load_env_tasks() -> None:
    if not RADAR_TASKS_JSON:
        return
    try:
        raw = json.loads(RADAR_TASKS_JSON)
        items = raw.get("tasks", raw) if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            raise ValueError("RADAR_TASKS_JSON must be a list or {tasks:[...]}")
        now = time.time()
        for item in items:
            task = RadarTaskIn.model_validate(item)
            if not task.enabled:
                continue
            key = task_key(task)
            runtime.tasks[key] = TaskState(
                task=task,
                last_position=task.baseline_position,
                next_due_ts=now,
            )
    except Exception as exc:
        runtime.last_error = f"RADAR_TASKS_JSON error: {type(exc).__name__}: {exc}"


@app.on_event("startup")
async def startup() -> None:
    load_env_tasks()
    asyncio.create_task(scheduler())


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": "ozon-live-radar",
        "version": APP_VERSION,
        "status": "ok",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    statuses = []
    for key, state in runtime.tasks.items():
        statuses.append(
            {
                "key": key,
                "article": state.task.article,
                "sku": state.task.sku,
                "query": state.task.query,
                "interval_min": state.task.interval_min,
                "position": state.last_position,
                "last_checked": state.last_checked_iso,
                "status": state.status,
                "last_error": state.last_error,
            }
        )
    return {
        "ok": True,
        "version": APP_VERSION,
        "configured_tasks": len(runtime.tasks),
        "event_seq": runtime.seq,
        "last_cycle": runtime.last_cycle_iso,
        "last_success": runtime.last_success_iso,
        "last_error": runtime.last_error,
        "proxy_configured": bool(OZON_PROXY),
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "tasks": statuses,
    }


@app.post("/config")
def set_config(payload: ConfigIn, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_auth(authorization)

    incoming: dict[str, RadarTaskIn] = {}
    for task in payload.tasks:
        if not task.enabled:
            continue
        key = task_key(task)
        incoming[key] = task

    # Remove disabled/deleted tasks.
    for key in list(runtime.tasks):
        if key not in incoming:
            del runtime.tasks[key]

    # Add/update tasks. Preserve current state when only thresholds/intervals changed.
    now = time.time()
    for key, task in incoming.items():
        existing = runtime.tasks.get(key)
        if existing is None:
            runtime.tasks[key] = TaskState(
                task=task,
                last_position=task.baseline_position,
                next_due_ts=now,
            )
        else:
            existing.task = task
            if existing.last_position is None and task.baseline_position is not None:
                existing.last_position = task.baseline_position
            existing.next_due_ts = min(existing.next_due_ts or now, now + task.interval_min * 60)

    return {
        "ok": True,
        "tasks": len(runtime.tasks),
        "server_time": now_iso(),
    }


@app.get("/events")
def events(
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=5000),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_auth(authorization)
    rows = [event for event in runtime.events if int(event["seq"]) > after][:limit]
    return {
        "ok": True,
        "after": after,
        "next_cursor": rows[-1]["seq"] if rows else after,
        "events": rows,
    }


@app.get("/state")
def state(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_auth(authorization)
    result = []
    for key, item in runtime.tasks.items():
        result.append(
            {
                "key": key,
                "article": item.task.article,
                "sku": item.task.sku,
                "query": item.task.query,
                "interval_min": item.task.interval_min,
                "drop_threshold": item.task.drop_threshold,
                "top_boundary": item.task.top_boundary,
                "max_position": item.task.max_position,
                "position": item.last_position,
                "last_checked": item.last_checked_iso,
                "status": item.status,
                "source": item.source,
                "endpoint": item.endpoint,
                "http_status": item.http_status,
                "response_ms": item.response_ms,
                "last_error": item.last_error,
            }
        )
    return {"ok": True, "tasks": result}
