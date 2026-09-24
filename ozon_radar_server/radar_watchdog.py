from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

HEALTH_URL = os.getenv("RADAR_HEALTH_URL", "http://127.0.0.1:8080/health")
STATE = Path(os.getenv("RADAR_WATCHDOG_STATE", "/tmp/ozon-watchdog-state.json"))
FAIL_THRESHOLD = int(os.getenv("RADAR_WATCHDOG_FAILS", "3"))
SCOUT_CMD = os.getenv("RADAR_PROXY_SCOUT_CMD", "python proxy_scout.py")


def health() -> dict:
    with urllib.request.urlopen(HEALTH_URL, timeout=10) as r:
        return json.load(r)


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {"fails": 0, "last_reason": "", "last_scout": ""}


def save_state(s: dict):
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2))


def main():
    s = load_state()
    try:
        h = health()
        broken = not h.get("backend_ok") or h.get("live_status") != "LIVE"
        reason = h.get("last_error") or h.get("egress_status") or h.get("live_status")
    except Exception as exc:
        broken = True
        reason = f"health_error: {type(exc).__name__}: {exc}"

    if broken:
        s["fails"] = int(s.get("fails", 0)) + 1
        s["last_reason"] = reason
    else:
        s["fails"] = 0
        s["last_reason"] = ""

    if s["fails"] >= FAIL_THRESHOLD:
        cp = subprocess.run(SCOUT_CMD, shell=True, text=True, capture_output=True, timeout=180)
        s["last_scout"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        s["scout_exit"] = cp.returncode
        s["scout_tail"] = (cp.stdout + "\n" + cp.stderr)[-4000:]

    save_state(s)
    print(json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    main()
