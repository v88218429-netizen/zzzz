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
from playwright.async_api import async_playwright

APP_VERSION = "1.2.4"
MAX_EVENTS = 20000
CHECK_LOOP_SECONDS = 3
SOURCE_NAME = "LIVE SERP · Ozon storefront JSON"

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


def build_ozon_proxy() -> str:
    direct = env("OZON_PROXY")
    if direct:
        return direct

    provider = env("OZON_PROXY_PROVIDER").lower()
    user = env("OZON_PROXY_USER")
    password = env("OZON_PROXY_PASS")
    host = env("OZON_PROXY_HOST")
    port = env("OZON_PROXY_PORT")
    city = env("OZON_PROXY_CITY") or "rostov_on_don"
    session_id = env("OZON_PROXY_SESSION") or "ozonradar1"

    if provider == "decodo" and user and password:
        host = host or "gate.decodo.com"
        port = port or "7000"
        prefix = user if user.startswith("user-") else "user-" + user
        username = f"{prefix}-country-ru-city-{city}-session-{session_id}"
        return (
            "http://"
            + urllib.parse.quote(username, safe="")
            + ":"
            + urllib.parse.quote(password, safe="")
            + "@"
            + host
            + ":"
            + port
        )

    return ""


RADAR_SECRET = env("RADAR_SECRET")
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", "BOT_TOKEN")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID", "OWNER_CHAT_ID")
OZON_PROXY = build_ozon_proxy()
RADAR_TASKS_JSON = env("RADAR_TASKS_JSON")
OZON_BROWSER_MODE = (env("OZON_BROWSER_MODE") or "auto").lower()
OZON_BROWSER_WARMUP_MS = max(5000, min(30000, int(env("OZON_BROWSER_WARMUP_MS") or "12000")))
CONFIRM_SECONDS = max(15, min(30, int(env("OZON_CONFIRM_SECONDS") or "20")))


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


class ProbeIn(BaseModel):
    query: str
    sku: str
    max_position: int = Field(default=30, ge=10, le=500)


@dataclass
class TaskState:
    task: RadarTaskIn
    last_position: int | None = None
    last_checked_ts: float = 0.0
    last_checked_iso: str = ""
    next_due_ts: float = 0.0
    alert_active: bool = False
    alert_kind: str = ""
    alert_origin_position: int | None = None
    last_alert_position: int | None = None
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

    # Ozon changes widget keys frequently; in current responses the product
    # grid may be tileGridDesktop-*, tileGrid2-*, searchResultsV2-* or even
    # an empty key. Detect the product grid by its content, not the widget name.
    found: list[str] = []
    seen: set[str] = set()

    for _, raw in states.items():
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
            sku = item.get("sku") or item.get("id") or item.get("skuId")
            sku_text = str(sku) if sku is not None else ""

            # Ignore navigation/separator items. Real product items normally
            # have sku/id and an action link or product-like state.
            if sku_text and sku_text.isdigit():
                action = item.get("action")
                has_product_shape = bool(
                    isinstance(action, dict) and action.get("link")
                ) or bool(item.get("mainState")) or bool(item.get("tileState"))
                if not has_product_shape:
                    continue
            else:
                blob = json.dumps(item, ensure_ascii=False)
                m = re.search(r"/product/[a-zA-Z0-9\\-_]+-(\\d{6,})/", blob)
                sku_text = m.group(1) if m else ""

            if sku_text and sku_text not in seen:
                seen.add(sku_text)
                found.append(sku_text)

    return found


class OzonClient:
    def __init__(self) -> None:
        self.session = curl_requests.Session(impersonate="chrome124")
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._browser_lock = asyncio.Lock()
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


    async def _reset_browser(self) -> None:
        page, context, browser, pw = self._page, self._context, self._browser, self._playwright
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        for obj in (page, context, browser):
            if obj is not None:
                try:
                    await obj.close()
                except Exception:
                    pass
        if pw is not None:
            try:
                await pw.stop()
            except Exception:
                pass

    async def _ensure_browser(self) -> None:
        if self._page is not None:
            try:
                if not self._page.is_closed():
                    return
            except Exception:
                pass
        await self._reset_browser()

        self._playwright = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--mute-audio",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-background-networking",
                "--lang=ru-RU",
            ],
        }
        # Playwright's chromium channel opts into the full Chromium/new-headless
        # path instead of the lightweight headless shell that Variti often detects.
        try:
            self._browser = await self._playwright.chromium.launch(channel="chromium", **launch_kwargs)
        except Exception:
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)

        context_kwargs: dict[str, Any] = {
            "locale": "ru-RU",
            "timezone_id": "Europe/Moscow",
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        if OZON_PROXY:
            parsed = urllib.parse.urlsplit(OZON_PROXY)
            if parsed.hostname:
                context_kwargs["proxy"] = {
                    "server": f"{parsed.scheme or 'http'}://{parsed.hostname}:{parsed.port or 80}",
                    **({"username": urllib.parse.unquote(parsed.username)} if parsed.username else {}),
                    **({"password": urllib.parse.unquote(parsed.password)} if parsed.password else {}),
                }

        self._context = await self._browser.new_context(**context_kwargs)
        self._page = await self._context.new_page()
        await self._page.goto(
            "https://www.ozon.ru/",
            wait_until="domcontentloaded",
            timeout=45000,
        )
        # Do not block images/fonts/styles: Variti challenge uses normal page resources.
        await self._page.wait_for_timeout(OZON_BROWSER_WARMUP_MS)
        title = (await self._page.title()).lower()
        if any(marker in title for marker in ("antibot", "ограничен", "нет соединения", "access denied", "challenge")):
            # Give Variti one extra window before declaring this egress blocked.
            await self._page.wait_for_timeout(8000)
            title = (await self._page.title()).lower()
            if any(marker in title for marker in ("antibot", "ограничен", "нет соединения", "access denied", "challenge")):
                raise RuntimeError(f"Variti challenge not passed: title={title[:120]}")

    async def _browser_request_page(self, path: str) -> tuple[dict[str, Any], str, int, int]:
        last_error = ""
        endpoints = [
            "/api/composer-api.bx/page/json/v2",
            "/api/entrypoint-api.bx/page/json/v2",
        ]

        async with self._browser_lock:
            for browser_attempt in range(2):
                await self._ensure_browser()
                for endpoint in endpoints:
                    started = time.perf_counter()
                    try:
                        payload = await self._page.evaluate(
                            """async ({endpoint, path}) => {
                              const url = endpoint + "?url=" + encodeURIComponent(path);
                              const response = await fetch(url, {
                                method: "GET",
                                credentials: "include",
                                headers: {
                                  "accept": "application/json"
                                }
                              });
                              return {
                                status: response.status,
                                url: response.url,
                                text: await response.text()
                              };
                            }""",
                            {"endpoint": endpoint, "path": path},
                        )
                    except Exception as exc:
                        last_error = f"browser {endpoint}: {type(exc).__name__}: {exc}"
                        continue

                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    status = int((payload or {}).get("status") or 0)
                    body = str((payload or {}).get("text") or "")
                    final_url = str((payload or {}).get("url") or endpoint)

                    lower = body.lower()
                    if status in (307, 401, 403, 429) or any(
                        marker in lower
                        for marker in (
                            "antibot",
                            "captcha",
                            "похоже, нет соединения",
                            "доступ ограничен",
                            "access denied",
                        )
                    ):
                        last_error = f"browser {endpoint}: HTTP {status or 'blocked'}"
                        continue
                    if status != 200:
                        last_error = f"browser {endpoint}: HTTP {status}"
                        continue
                    try:
                        data = json.loads(body)
                    except Exception:
                        last_error = f"browser {endpoint}: invalid JSON"
                        continue
                    if not isinstance(data, dict):
                        last_error = f"browser {endpoint}: non-object JSON"
                        continue
                    return data, final_url, status, elapsed_ms

                # Session/challenge may have expired. Rebuild one time only.
                await self._reset_browser()

        raise RuntimeError(last_error or "browser Ozon source failed")

    async def position_async(self, query: str, sku: str, max_position: int) -> dict[str, Any]:
        target = str(sku)
        seen: list[str] = []
        seen_set: set[str] = set()
        pages = max(1, min(10, (max_position + 35) // 36))
        total_ms = 0
        last_endpoint = ""
        last_http = 0

        for page in range(1, pages + 1):
            # Keep the inner search URL unescaped here. The outer composer
            # request encodes the whole `url` parameter exactly once.
            path = f"/search/?text={query}&from_global=true"
            if page > 1:
                path += f"&page={page}"

            browser_error = ""
            data = None
            endpoint = ""
            http_status = 0
            elapsed_ms = 0

            if OZON_BROWSER_MODE in ("auto", "browser", "playwright"):
                try:
                    data, endpoint, http_status, elapsed_ms = await self._browser_request_page(path)
                except Exception as exc:
                    browser_error = f"{type(exc).__name__}: {exc}"
                    if OZON_BROWSER_MODE in ("browser", "playwright"):
                        raise

            if data is None:
                try:
                    data, endpoint, http_status, elapsed_ms = await asyncio.to_thread(
                        self._request_page, path
                    )
                except Exception as exc:
                    if browser_error:
                        raise RuntimeError(
                            f"browser={browser_error}; direct={type(exc).__name__}: {exc}"
                        ) from exc
                    raise

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

    def position(self, query: str, sku: str, max_position: int) -> dict[str, Any]:
        target = str(sku)
        seen: list[str] = []
        seen_set: set[str] = set()
        pages = max(1, min(10, (max_position + 35) // 36))
        total_ms = 0
        last_endpoint = ""
        last_http = 0

        for page in range(1, pages + 1):
            # Keep the inner search URL unescaped here. The outer composer
            # request encodes the whole `url` parameter exactly once.
            path = f"/search/?text={query}&from_global=true"
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


async def ozon_position(client: Any, query: str, sku: str, max_position: int) -> dict[str, Any]:
    async_method = getattr(client, "position_async", None)
    if callable(async_method):
        return await async_method(query, sku, max_position)
    return await asyncio.to_thread(client.position, query, sku, max_position)


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
    if position is None or position > max_position:
        return f">{max_position}"
    return str(position)


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


async def _measure_confirmed(task: RadarTaskIn, previous: int | None) -> dict[str, Any]:
    first = await ozon_position(ozon, task.query, task.sku, task.max_position)
    first_pos = first["position"]
    first_eff = first_pos if first_pos is not None else task.max_position + 1

    needs_confirmation = first_pos is None
    if previous is not None:
        drop = first_eff - previous
        crossed = previous <= task.top_boundary and first_eff > task.top_boundary
        needs_confirmation = needs_confirmation or crossed or drop >= task.drop_threshold

    if not needs_confirmation:
        first["position_effective"] = first_eff
        first["confirmed"] = True
        return first

    await asyncio.sleep(CONFIRM_SECONDS)
    second = await ozon_position(ozon, task.query, task.sku, task.max_position)
    second_pos = second["position"]

    if first_pos is None and second_pos is None:
        second["position"] = task.max_position + 1
        second["position_effective"] = task.max_position + 1
        second["position_text"] = f">{task.max_position}"
        second["status"] = f"OUTSIDE_TOP_{task.max_position}_CONFIRMED"
        second["confirmed"] = True
        return second

    if first_pos is not None and second_pos is None:
        # A sudden drop followed by one miss is not enough evidence to convert
        # the measurement to max_position+1. Confirm the miss one more time.
        await asyncio.sleep(CONFIRM_SECONDS)
        third = await ozon_position(ozon, task.query, task.sku, task.max_position)
        if third["position"] is None:
            third["position"] = task.max_position + 1
            third["position_effective"] = task.max_position + 1
            third["position_text"] = f">{task.max_position}"
            third["status"] = f"OUTSIDE_TOP_{task.max_position}_CONFIRMED"
            third["confirmed"] = True
            return third
        third["position_effective"] = third["position"]
        third["confirmed"] = True
        return third

    second["position_effective"] = second_pos
    second["confirmed"] = True
    return second


async def check_one(key: str, state: TaskState) -> None:
    task = state.task
    previous = state.last_position
    checked_iso = now_iso()

    try:
        result = await _measure_confirmed(task, previous)
        position = int(result["position_effective"])
        display_position = result.get("position_text") or str(position)

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
        delta = None if previous is None else previous - position

        if previous is not None:
            drop = position - previous
            crossed_out = previous <= task.top_boundary and position > task.top_boundary
            crossed_in = previous > task.top_boundary and position <= task.top_boundary
            material_drop = drop >= task.drop_threshold

            if not state.alert_active and (crossed_out or material_drop):
                alert_type = "OUT_TOP" if crossed_out else "DROP"
                state.alert_active = True
                state.alert_kind = alert_type
                state.alert_origin_position = previous
                state.last_alert_position = position
            elif state.alert_active:
                recovered = (
                    (state.alert_kind == "OUT_TOP" and crossed_in)
                    or (
                        state.alert_kind == "DROP"
                        and state.alert_origin_position is not None
                        and position <= state.alert_origin_position
                    )
                )
                if recovered:
                    alert_type = "RECOVERY"
                    state.alert_active = False
                elif (
                    state.last_alert_position is not None
                    and position - state.last_alert_position >= task.drop_threshold
                ):
                    alert_type = "DROP"
                    state.last_alert_position = position

            if alert_type:
                alert_message = make_alert(task, previous, position, alert_type)
                state.last_alert_iso = checked_iso
                if alert_type == "RECOVERY":
                    state.alert_kind = ""
                    state.alert_origin_position = None
                    state.last_alert_position = None

        telegram_sent = False
        telegram_error = ""
        if alert_message and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            try:
                await asyncio.to_thread(telegram_send, alert_message)
                telegram_sent = True
            except Exception as exc:
                telegram_error = f"{type(exc).__name__}: {exc}"

        add_event(
            {
                "kind": "CHECK",
                "checked_at": checked_iso,
                "key": key,
                "article": task.article,
                "sku": task.sku,
                "query": task.query,
                "position": position,
                "position_text": display_position,
                "previous": previous,
                "delta": delta,
                "status": state.status,
                "source": state.source,
                "endpoint": state.endpoint,
                "http_status": state.http_status,
                "response_ms": state.response_ms,
                "confirmed": bool(result.get("confirmed")),
                "alert_type": alert_type,
                "alert_message": alert_message,
                "telegram_sent": telegram_sent,
            }
        )

        if telegram_error:
            add_event(
                {
                    "kind": "TELEGRAM_ERROR",
                    "checked_at": checked_iso,
                    "key": key,
                    "article": task.article,
                    "sku": task.sku,
                    "query": task.query,
                    "status": "Telegram error: " + telegram_error,
                }
            )

        state.last_position = position

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
    source_ready = bool(runtime.last_success_iso) and not runtime.last_error
    return {
        "ok": True,
        "backend_ok": True,
        "live_ready": source_ready,
        "live_status": "LIVE" if source_ready else "LIVE_NЕТ",
        "version": APP_VERSION,
        "configured_tasks": len(runtime.tasks),
        "event_seq": runtime.seq,
        "last_cycle": runtime.last_cycle_iso,
        "last_success": runtime.last_success_iso,
        "last_error": runtime.last_error,
        "proxy_configured": bool(OZON_PROXY),
        "browser_mode": OZON_BROWSER_MODE,
        "browser_ready": bool(getattr(ozon, "_page", None)),
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


@app.post("/probe")
async def probe(
    payload: ProbeIn,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_auth(authorization)
    checked_at = now_iso()
    started = time.perf_counter()
    try:
        result = await ozon_position(
            ozon,
            payload.query,
            payload.sku,
            payload.max_position,
        )
        return {
            "ok": True,
            "checked_at": checked_at,
            "query": payload.query,
            "sku": payload.sku,
            "proxy_configured": bool(OZON_PROXY),
            "result": result,
            "total_ms": int((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:
        return {
            "ok": False,
            "checked_at": checked_at,
            "query": payload.query,
            "sku": payload.sku,
            "proxy_configured": bool(OZON_PROXY),
            "error": f"{type(exc).__name__}: {exc}",
            "total_ms": int((time.perf_counter() - started) * 1000),
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
