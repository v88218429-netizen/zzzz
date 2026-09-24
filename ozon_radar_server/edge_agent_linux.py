from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import websocket

HOST = "0.0.0.0"
PORT = 8900
CDP = "http://127.0.0.1:9222"
_seq = 0


def _json(url: str):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r)


def _target_ws() -> str:
    targets = _json(CDP + "/json")
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise RuntimeError("No Chromium page target")
    return pages[0]["webSocketDebuggerUrl"]


def _cmd(ws, method: str, params=None):
    global _seq
    _seq += 1
    msg_id = _seq
    ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    while True:
        data = json.loads(ws.recv())
        if data.get("id") == msg_id:
            if "error" in data:
                raise RuntimeError(str(data["error"]))
            return data.get("result") or {}


def _eval(ws, expression: str):
    out = _cmd(ws, "Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": True,
    })
    return ((out.get("result") or {}).get("value"))


def get_position(query: str, sku: str, max_position: int = 100) -> dict:
    target = str(sku)
    seen, seen_set = [], set()
    pages = max(1, min(10, (max_position + 35) // 36))
    ws = websocket.create_connection(_target_ws(), timeout=60)
    try:
        _cmd(ws, "Runtime.enable")
        _cmd(ws, "Page.enable")

        def collect_visible():
            hrefs = _eval(ws, """Array.from(document.querySelectorAll('a[href*="/product/"]')).map(a=>a.href||a.getAttribute('href')||'')""") or []
            for href in hrefs:
                m = re.search(r"-(\d{6,})(?:/|\?|$)", str(href))
                if not m:
                    continue
                val = m.group(1)
                if val in seen_set:
                    continue
                seen_set.add(val)
                seen.append(val)
                if val == target:
                    return {
                        "ok": True,
                        "position": len(seen),
                        "checked_depth": len(seen),
                        "source": "edge-chromium-cdp",
                    }
                if len(seen) >= max_position:
                    break
            return None

        for page_no in range(1, pages + 1):
            url = "https://www.ozon.ru/search/?text=" + urllib.parse.quote(query)
            if page_no > 1:
                url += f"&page={page_no}"
            _cmd(ws, "Page.navigate", {"url": url})
            time.sleep(8)

            for step in range(12):
                found = collect_visible()
                if found:
                    found["page"] = page_no
                    found["scroll_step"] = step
                    return found
                if len(seen) >= max_position:
                    break
                _eval(ws, "window.scrollBy(0,1100); true")
                time.sleep(1)

            found = collect_visible()
            if found:
                found["page"] = page_no
                found["scroll_step"] = 12
                return found
            if len(seen) >= max_position:
                break

        return {
            "ok": True,
            "position": None,
            "status": f"NOT_FOUND_TOP_{max_position}",
            "checked_depth": len(seen),
            "source": "edge-chromium-cdp",
        }
    finally:
        ws.close()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/health":
            self._send({"ok": True, "service": "ozon-edge-linux-agent"})
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
            result = get_position(query, sku, max_position)
            self._send(result, 200 if result.get("ok") else 502)
        except Exception as exc:
            self._send({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)

    def log_message(self, *_):
        pass

    def _send(self, payload: dict, status=200):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    HTTPServer((HOST, PORT), Handler).serve_forever()
