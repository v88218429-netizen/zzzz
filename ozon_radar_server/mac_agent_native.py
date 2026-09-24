from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

HOST = "0.0.0.0"
PORT = 8900
BOUND_WINDOW_ID: int | None = None
BOUND_TAB_INDEX: int | None = None
REQUEST_LOCK = threading.Lock()

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

def bind_active_tab() -> tuple[int, int]:
    global BOUND_WINDOW_ID, BOUND_TAB_INDEX
    script = '''
tell application "Google Chrome"
  if (count of windows) = 0 then return "0||0"
  set w to front window
  set wi to id of w
  set ti to active tab index of w
  return (wi as text) & "||" & (ti as text)
end tell
'''
    out = run_osascript(script, timeout=20)
    parts = out.split("||", 1)
    wi = int(parts[0] or "0")
    ti = int(parts[1] or "0")
    if wi <= 0 or ti <= 0:
        raise RuntimeError("No active Google Chrome tab to bind")
    BOUND_WINDOW_ID = wi
    BOUND_TAB_INDEX = ti
    return wi, ti


def get_bound_tab() -> tuple[int, int]:
    if BOUND_WINDOW_ID is None or BOUND_TAB_INDEX is None:
        return bind_active_tab()

    script = f'''
tell application "Google Chrome"
  repeat with w in windows
    if (id of w) is {BOUND_WINDOW_ID} then
      if (count of tabs of w) >= {BOUND_TAB_INDEX} then
        return "{BOUND_WINDOW_ID}||{BOUND_TAB_INDEX}"
      end if
    end if
  end repeat
end tell
return "0||0"
'''
    out = run_osascript(script, timeout=20)
    if out == "0||0":
        raise RuntimeError("Bound Chrome tab was closed. Restart agent while the dedicated Ozon tab is active.")
    return BOUND_WINDOW_ID, BOUND_TAB_INDEX


def js(expr: str) -> str:
    wi, ti = get_bound_tab()
    escaped = expr.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
tell application "Google Chrome"
  set w to first window whose id is {wi}
  set t to tab {ti} of w
  execute t javascript "{escaped}"
end tell
'''
    return run_osascript(script, timeout=60)


def navigate(url: str) -> None:
    wi, ti = get_bound_tab()
    safe = url.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
tell application "Google Chrome"
  set w to first window whose id is {wi}
  set URL of tab {ti} of w to "{safe}"
end tell
'''
    run_osascript(script, timeout=30)


def title_and_url() -> tuple[str, str]:
    wi, ti = get_bound_tab()
    script = f'''
tell application "Google Chrome"
  set w to first window whose id is {wi}
  set t to title of tab {ti} of w
  set u to URL of tab {ti} of w
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
            with REQUEST_LOCK:
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
    wi, ti = bind_active_tab()
    print(f"Ozon Native Chrome Agent bound to Chrome window {wi}, tab {ti}")
    print(f"Listening on http://127.0.0.1:{PORT}")
    HTTPServer((HOST, PORT), Handler).serve_forever()
