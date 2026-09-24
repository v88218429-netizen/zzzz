from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

from agents import Agent, Runner, function_tool, SQLiteSession

BASE = Path(__file__).resolve().parent
HEALTH_URL = os.getenv("RADAR_HEALTH_URL", "http://127.0.0.1:8080/health")
PROXY_RESULT = Path(os.getenv("PROXY_SCOUT_RESULT", "/tmp/ozon-proxy-scout.json"))
SESSION_DB = os.getenv("AI_OPERATOR_SESSION_DB", "/tmp/ozon-ai-operator.db")


def _http_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)


@function_tool
def get_radar_health() -> str:
    """Return current Ozon radar health and errors."""
    try:
        return json.dumps(_http_json(HEALTH_URL), ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)


@function_tool
def get_proxy_scout_result() -> str:
    """Return latest free proxy scout results."""
    if not PROXY_RESULT.exists():
        return json.dumps({"ok": False, "error": "no proxy scout result"}, ensure_ascii=False)
    return PROXY_RESULT.read_text(encoding="utf-8")[-12000:]


@function_tool
def run_proxy_scout() -> str:
    """Run a bounded free-proxy scout against public Ozon pages only."""
    cp = subprocess.run(
        ["python", str(BASE / "proxy_scout.py")],
        text=True,
        capture_output=True,
        timeout=240,
    )
    return json.dumps({
        "exit": cp.returncode,
        "stdout_tail": cp.stdout[-12000:],
        "stderr_tail": cp.stderr[-4000:],
    }, ensure_ascii=False)


operator = Agent(
    name="Ozon Radar Incident Operator",
    instructions=(
        "You operate an Ozon live-position monitoring system. "
        "Your job is to diagnose incidents, not to fabricate success. "
        "Always inspect radar health first. If egress or Ozon access is broken, "
        "use the proxy scout and compare candidates. Free/public proxies are allowed "
        "ONLY for public Ozon pages: never send authentication cookies, seller tokens, "
        "account credentials, or private traffic through them. "
        "Prefer reversible configuration changes over code changes. "
        "Never claim a route works until an actual Ozon position probe succeeds. "
        "Return a compact incident report with: root cause hypothesis, evidence, "
        "next safe action, and whether human intervention is required."
    ),
    tools=[get_radar_health, get_proxy_scout_result, run_proxy_scout],
)


def run_incident(prompt: str) -> str:
    session = SQLiteSession("ozon-radar-operator", SESSION_DB)
    result = Runner.run_sync(operator, prompt, session=session)
    return result.final_output


if __name__ == "__main__":
    prompt = os.getenv(
        "AI_OPERATOR_PROMPT",
        "Investigate the current radar incident and propose the next safe recovery action."
    )
    print(run_incident(prompt))
