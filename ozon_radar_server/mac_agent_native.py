from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = 8900

def run_osascript(script: str, timeout: int = 120) -> str:
    p = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "osascript failed").strip())
    return (p.stdout or "").strip()

def js(expr: str) -> str:
    escaped = expr.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
tell application "Google Chrome"
  if (count of windows) = 0 then make new window
  execute active tab of front window javascript "{escaped}"
end tell
'''
    return run_osascript(script, timeout=60)

def navigate(url: str) -> None:
    safe = url.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
tell application "Google Chrome"
  activate
  if (count of windows) = 0 then make new window
  set URL of active tab of front window to "{safe}"
end tell
'''
    run_osascript(script, timeout=30)

def title_and_url() -> tuple[str, str]:
    script = '''
tell application "Google Chrome"
  if (count of windows) = 0 then return "||"
  set t to title of active tab of front window
  set u to URL of active tab of front window
  return t & "||" & u
end tell
'''
    out = run_osascript(script, timeout=30)
    parts = out.split("||", 1)
    return (parts[0] if parts else "", parts[1] if len(parts) > 1 else "")

def get_position(query: str, sku: str, max_position: int = 100) -> dict:
    target = str(sku)
    seen: list[str] = []
    seen_set: set[str] = set()
    pages = max(1, min(10, (max_position + 35) // 36))

    for page_no in range(1, pages + 1):
        url = "https://www.ozon.ru/search/?text=" + urllib.parse.quote(query)
        if page_no > 1:
            url += f"&page={page_no}"

        navigate(url)
        time.sleep(8)

        for _ in range(7):
            try:
                js("window.scrollBy(0, 1400); 'ok'")
            except Exception as exc:
                if "JavaScript from Apple Events" in str(exc) or "javascript" in str(exc).lower():
                    raise RuntimeError(
                        "Chrome blocks JavaScript from Apple Events. "
                        "Enable View → Developer → Allow JavaScript from Apple Events."
                    ) from exc
            time.sleep(0.8)

        title, current_url = title_and_url()
        low = (title + " " + current_url).lower()
        if any(x in low for x in ("нет соединения", "доступ ограничен", "antibot", "challenge")):
            return {"ok": False, "error": f"ozon page blocked: {title[:120]}"}

        raw = js(
            "JSON.stringify(Array.from(document.querySelectorAll('a[href*=\\\"/product/\\\"]')).map(a=>a.href||a.getAttribute('href')||''))"
        )
        try:
            hrefs = json.loads(raw)
        except Exception:
            hrefs = []

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
                return {
                    "ok": True,
                    "position": len(seen),
                    "checked_depth": len(seen),
                    "page": page_no,
                    "source": "native-chrome-applescript",
                }
            if len(seen) >= max_position:
                break
        if len(seen) >= max_position:
            break

    return {
        "ok": True,
        "position": None,
        "status": f"NOT_FOUND_TOP_{max_position}",
        "checked_depth": len(seen),
        "source": "native-chrome-applescript",
    }

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/health":
            self._send({"ok": True, "service": "ozon-mac-native-agent"})
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
    print(f"Ozon Native Chrome Agent listening on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
