#!/usr/bin/env python3
import json
import pathlib
import re
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "transcripts"


def fetch_json(url, timeout=90):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def stamp(sec):
    try:
        sec = int(float(sec or 0))
    except Exception:
        sec = 0
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def repair(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    body = text.split("## Transcript", 1)[-1]
    has_timestamp = bool(re.search(r"(?m)^\[\d{1,2}:\d{2}(?::\d{2})?\]", body))
    bad_notice = "calling this api at high volume" in body.lower() or "stable transcript api" in body.lower()
    if has_timestamp and not bad_notice:
        return False

    m = re.search(r"^- Video ID:\s*(\S+)\s*$", text, re.M)
    if not m:
        return False
    vid = m.group(1)
    params = urlencode({"video_url": vid, "lang": "ru"})
    data = fetch_json(f"https://api.freetranscriptapi.com/v1/transcript?{params}")
    rows = data.get("transcript") or data.get("segments") or []
    if not rows:
        raise RuntimeError(f"No FreeTranscriptAPI transcript for {vid}: {data.get('error')}")

    lines = []
    for row in rows:
        t = re.sub(r"\s+", " ", str(row.get("text", ""))).strip()
        if t:
            lines.append(f"[{stamp(row.get('start', row.get('offset', 0)))}] {t}")
    if not lines:
        raise RuntimeError(f"Empty transcript rows for {vid}")

    title = data.get("title") or ""
    if not title:
        mt = re.search(r"^#\s+(.+)$", text, re.M)
        title = mt.group(1).strip() if mt else vid
    source = f"https://www.youtube.com/watch?v={vid}"
    new = [
        f"# {title}", "", f"- Source: {source}", f"- Video ID: {vid}",
        "- Transcript method: FreeTranscriptAPI (repair)",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}", "", "## Transcript", "",
        *lines, ""
    ]
    path.write_text("\n".join(new), encoding="utf-8")
    print(f"REPAIRED {path.name}")
    return True


def main():
    repaired = 0
    failed = 0
    for path in sorted(OUT.glob("*youtube_*.md")):
        try:
            repaired += int(repair(path))
        except Exception as exc:
            failed += 1
            print(f"REPAIR ERROR {path.name}: {exc}")
    print(f"Repair complete: repaired={repaired}, failed={failed}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
