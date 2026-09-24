from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.async_api import async_playwright

HOST = "0.0.0.0"
PORT = 8900
PROFILE = str(Path.home() / ".ozon_radar_chrome_profile")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

async def get_position(query: str, sku: str, max_position: int = 100) -> dict:
    target = str(sku)
    seen: list[str] = []
    seen_set: set[str] = set()

    async with async_playwright() as p:
        launch_kwargs = dict(
            user_data_dir=PROFILE,
            headless=False,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1440, "height": 1000},
            args=["--no-first-run", "--no-default-browser-check", "--disable-notifications"],
        )
        if Path(CHROME).exists():
            launch_kwargs["executable_path"] = CHROME

        context = await p.chromium.launch_persistent_context(**launch_kwargs)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            pages = max(1, min(10, (max_position + 35) // 36))
            for page_no in range(1, pages + 1):
                url = "https://www.ozon.ru/search/?text=" + urllib.parse.quote(query)
                if page_no > 1:
                    url += f"&page={page_no}"

                await page.goto(url, wait_until="domcontentloaded", timeout=90000)
                await page.wait_for_timeout(8000)

                for _ in range(5):
                    await page.mouse.wheel(0, 1200)
                    await page.wait_for_timeout(700)

                title = (await page.title()).lower()
                if any(x in title for x in ("нет соединения", "доступ ограничен", "antibot", "challenge")):
                    return {"ok": False, "error": f"challenge: {title[:120]}"}

                hrefs = await page.locator('a[href*="/product/"]').evaluate_all(
                    """els => els.map(a => a.href || a.getAttribute('href') || '')"""
                )

                page_skus: list[str] = []
                for href in hrefs:
                    m = re.search(r"-(\d{6,})(?:/|\?|$)", str(href))
                    if not m:
                        continue
                    val = m.group(1)
                    if val not in page_skus:
                        page_skus.append(val)

                for val in page_skus:
                    if val in seen_set:
                        continue
                    seen_set.add(val)
                    seen.append(val)
                    if val == target:
                        return {"ok": True, "position": len(seen), "checked_depth": len(seen), "page": page_no}
                    if len(seen) >= max_position:
                        break
                if len(seen) >= max_position:
                    break

            return {"ok": True, "position": None, "status": f"NOT_FOUND_TOP_{max_position}", "checked_depth": len(seen)}
        finally:
            await context.close()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/health":
            self._send({"ok": True, "service": "ozon-mac-agent"})
            return
        if parsed.path != "/position":
            self._send({"ok": False, "error": "not found"}, 404)
            return

        qs = urllib.parse.parse_qs(parsed.query)
        query = (qs.get("query") or [""])[0]
        sku = (qs.get("sku") or [""])[0]
        try:
            max_position = int((qs.get("max_position") or ["100"])[0])
        except Exception:
            max_position = 100

        if not query or not sku:
            self._send({"ok": False, "error": "query and sku required"}, 400)
            return

        try:
            result = asyncio.run(get_position(query, sku, max_position))
            self._send(result, 200 if result.get("ok") else 502)
        except Exception as exc:
            self._send({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)

    def log_message(self, fmt, *args):
        pass

    def _send(self, payload: dict, status: int = 200):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

if __name__ == "__main__":
    print(f"Ozon Mac Agent listening on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
