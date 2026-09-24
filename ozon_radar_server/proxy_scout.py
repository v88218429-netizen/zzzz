from __future__ import annotations

import concurrent.futures
import json
import os
import socket
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from curl_cffi import requests

SOURCES = [
    ("proxmint", "https://raw.githubusercontent.com/proxmint/free-proxy-list/main/proxies/all.txt"),
    ("proxio", "https://raw.githubusercontent.com/proxio-io/proxy-list/main/all.txt"),
    ("proxyscrape", "https://raw.githubusercontent.com/ProxyScrape/free-proxy-list/main/proxies/all.txt"),
]

RESULT_PATH = Path(os.getenv("PROXY_SCOUT_RESULT", "/tmp/ozon-proxy-scout.json"))
MAX_CANDIDATES = int(os.getenv("PROXY_SCOUT_MAX", "200"))
PARALLELISM = int(os.getenv("PROXY_SCOUT_PARALLEL", "20"))
TIMEOUT = float(os.getenv("PROXY_SCOUT_TIMEOUT", "7"))


@dataclass
class Candidate:
    proxy: str
    source: str


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ozon-proxy-scout/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def normalize(line: str) -> str | None:
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    if "://" not in s:
        s = "http://" + s
    scheme = s.split("://", 1)[0].lower()
    if scheme not in {"http", "https", "socks4", "socks5"}:
        return None
    return s


def load_candidates() -> list[Candidate]:
    out: list[Candidate] = []
    seen: set[str] = set()
    per_source = max(25, MAX_CANDIDATES // max(1, len(SOURCES)))
    for name, url in SOURCES:
        try:
            lines = fetch_text(url).splitlines()
        except Exception:
            continue
        count = 0
        for line in lines:
            p = normalize(line)
            if not p or p in seen:
                continue
            seen.add(p)
            out.append(Candidate(p, name))
            count += 1
            if count >= per_source:
                break
    return out[:MAX_CANDIDATES]


def test_proxy(c: Candidate) -> dict:
    started = time.perf_counter()
    proxy = c.proxy
    proxies = {"http": proxy, "https": proxy}
    row = {
        "proxy": proxy,
        "source": c.source,
        "ok": False,
        "ip_ok": False,
        "ozon_front_ok": False,
        "ozon_api_status": None,
        "latency_ms": None,
        "error": "",
    }
    try:
        r = requests.get(
            "https://api.ipify.org?format=json",
            proxies=proxies,
            impersonate="chrome124",
            timeout=TIMEOUT,
        )
        row["ip_ok"] = r.status_code == 200
        if not row["ip_ok"]:
            row["error"] = f"ipify_http_{r.status_code}"
            return row

        r2 = requests.get(
            "https://www.ozon.ru/",
            proxies=proxies,
            impersonate="chrome124",
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        row["ozon_front_ok"] = r2.status_code in {200, 301, 302, 307, 308}

        r3 = requests.get(
            "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2",
            params={"url": "/search/?text=лопата%20садовая"},
            proxies=proxies,
            impersonate="chrome124",
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        row["ozon_api_status"] = int(r3.status_code)
        row["ok"] = bool(row["ozon_front_ok"] and r3.status_code == 200)
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        row["latency_ms"] = int((time.perf_counter() - started) * 1000)
    return row


def score(r: dict) -> tuple:
    return (
        0 if r["ok"] else 1,
        0 if r["ozon_front_ok"] else 1,
        0 if r["ip_ok"] else 1,
        r.get("latency_ms") or 999999,
    )


def run() -> dict:
    candidates = load_candidates()
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=PARALLELISM) as ex:
        futures = [ex.submit(test_proxy, c) for c in candidates]
        for f in concurrent.futures.as_completed(futures):
            try:
                results.append(f.result())
            except Exception as exc:
                results.append({"ok": False, "error": str(exc)})

    results.sort(key=score)
    payload = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "candidate_count": len(candidates),
        "working_ozon_api": sum(1 for r in results if r.get("ok")),
        "front_only": sum(1 for r in results if r.get("ozon_front_ok")),
        "best": results[:20],
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
