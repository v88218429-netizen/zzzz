#!/usr/bin/env python3
import json
import pathlib
import re
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.request import Request, urlopen

ROOT = pathlib.Path(__file__).resolve().parent
REQ = ROOT / "requests"
OUT = ROOT / "transcripts"
COLL = ROOT / "collections"
STATE = ROOT / "processed"
for d in (OUT, COLL, STATE):
    d.mkdir(exist_ok=True)


def run(cmd):
    p = subprocess.run(cmd, text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip() or "command failed")
    return p.stdout


def video_id(url):
    u = urlparse(url)
    host = u.netloc.lower()
    if "youtube.com" in host:
        vid = parse_qs(u.query).get("v", [None])[0]
        if vid:
            return vid
        parts = [p for p in u.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in {"shorts", "live", "embed"}:
            return parts[1]
    if "youtu.be" in host:
        return u.path.strip("/").split("/")[0]
    return None


def youtube_search(query, limit):
    target = f"ytsearch{max(1, min(int(limit), 50))}:{query}"
    raw = run(["yt-dlp", "--flat-playlist", "--dump-single-json", "--no-warnings", target])
    data = json.loads(raw)
    items = []
    for e in (data.get("entries") or [])[:limit]:
        vid = e.get("id")
        if not vid:
            continue
        items.append({
            "url": f"https://www.youtube.com/watch?v={vid}",
            "video_id": vid,
            "title": e.get("title") or "",
            "uploader": e.get("uploader") or e.get("channel") or "",
            "duration": e.get("duration"),
        })
    return items


def expand(data):
    if data.get("search"):
        return youtube_search(data["search"], int(data.get("limit", 15))), "search"
    if data.get("url") and video_id(data["url"]):
        return [{"url": data["url"], "video_id": video_id(data["url"])}], "single"
    if data.get("urls"):
        items = []
        for u in data["urls"]:
            vid = video_id(u)
            if vid:
                items.append({"url": u, "video_id": vid})
        return items, "batch"
    return [], None


def fetch_text(url, timeout=90):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/plain,text/markdown,application/json,*/*"})
    with urlopen(req, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8", errors="replace")


def from_youtube_transcript_ai(vid, lang="ru"):
    urls = [
        f"https://youtube-transcript.ai/transcript/{vid}.txt?lang={lang}",
        f"https://youtube-transcript.ai/transcript/{vid}.txt",
    ]
    last = None
    for url in urls:
        try:
            status, ctype, text = fetch_text(url)
            if status == 200 and len(text.strip()) > 100 and "transcript" in text.lower():
                title = ""
                m = re.search(r"^# Transcript:\s*(.+)$", text, re.M)
                if m:
                    title = m.group(1).strip()
                body = text
                # Keep timestamped body, but remove service metadata header when recognizable.
                ts = re.search(r"(?m)^\[\d{1,2}:\d{2}(?::\d{2})?\]", text)
                if ts:
                    body = text[ts.start():].strip()
                return {"text": body, "title": title, "method": "youtube-transcript.ai"}
        except Exception as exc:
            last = exc
    if last:
        raise last
    raise RuntimeError("youtube-transcript.ai returned no transcript")


def from_free_transcript_api(vid, lang="ru"):
    params = urlencode({"video_url": vid, "lang": lang})
    status, ctype, text = fetch_text(f"https://api.freetranscriptapi.com/v1/transcript?{params}")
    data = json.loads(text)
    rows = data.get("transcript") or data.get("segments") or []
    if not rows:
        raise RuntimeError(data.get("error") or "FreeTranscriptAPI returned no transcript")
    lines = []
    for row in rows:
        t = str(row.get("text", "")).strip()
        if not t:
            continue
        start = row.get("start", 0)
        try:
            sec = int(float(start))
        except Exception:
            sec = 0
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        stamp = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        lines.append(f"[{stamp}] {t}")
    if not lines:
        raise RuntimeError("FreeTranscriptAPI returned empty rows")
    return {
        "text": "\n".join(lines),
        "title": data.get("title") or "",
        "method": "FreeTranscriptAPI",
    }


def fetch_transcript(vid, lang="ru"):
    errors = []
    for fn in (from_youtube_transcript_ai, from_free_transcript_api):
        try:
            return fn(vid, lang)
        except Exception as exc:
            errors.append(f"{fn.__name__}: {exc}")
    raise RuntimeError(" | ".join(errors))


def slug_for(stem, idx, vid, total):
    if total == 1:
        return stem
    return f"{stem}__{idx:02d}_youtube_{vid}"


def write_transcript(item, stem, idx, total, lang):
    vid = item["video_id"]
    slug = slug_for(stem, idx, vid, total)
    path = OUT / f"{slug}.md"
    # If a valid transcript already exists, reuse it.
    if path.exists() and path.stat().st_size > 500:
        return {
            "path": str(path.relative_to(ROOT)), "url": item["url"],
            "title": item.get("title") or vid, "duration": item.get("duration"),
            "method": "existing",
        }
    result = fetch_transcript(vid, lang)
    title = result.get("title") or item.get("title") or vid
    md = [
        f"# {title}", "",
        f"- Source: https://www.youtube.com/watch?v={vid}",
        f"- Video ID: {vid}",
        f"- Transcript method: {result['method']}",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        "", "## Transcript", "", result["text"].strip(), ""
    ]
    path.write_text("\n".join(md), encoding="utf-8")
    return {
        "path": str(path.relative_to(ROOT)), "url": f"https://www.youtube.com/watch?v={vid}",
        "title": title, "duration": item.get("duration"), "method": result["method"],
    }


def write_collection(stem, mode, data, results, errors):
    path = COLL / f"{stem}.md"
    lines = [f"# Collection: {stem}", "", f"- Mode: {mode}"]
    if data.get("search"):
        lines.append(f"- Search: {data['search']}")
    lines += [
        f"- Requested videos: {len(results)+len(errors)}",
        f"- Completed: {len(results)}",
        f"- Errors: {len(errors)}",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        "", "## Videos", ""
    ]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}**")
        lines.append(f"   - Source: {r['url']}")
        lines.append(f"   - Transcript: `{r['path']}`")
        lines.append(f"   - Method: {r['method']}")
    if errors:
        lines += ["", "## Errors", ""]
        for e in errors:
            lines.append(f"- {e['url']}: {e['error']}")
    path.write_text("\n".join(lines)+"\n", encoding="utf-8")
    return path


def process_file(f):
    data = json.loads(f.read_text(encoding="utf-8"))
    items, mode = expand(data)
    if not items:
        return True
    lang = data.get("language", "ru")
    results, errors = [], []
    total = len(items)
    for i, item in enumerate(items, 1):
        try:
            print(f"PUBLIC FALLBACK [{i}/{total}] {item['url']}")
            results.append(write_transcript(item, f.stem, i, total, lang))
        except Exception as exc:
            print(f"PUBLIC FALLBACK ERROR {item['url']}: {exc}")
            errors.append({"url": item["url"], "error": str(exc)[:1200]})
        time.sleep(0.15)
    coll = write_collection(f.stem, mode, data, results, errors)
    if not errors:
        state = {
            "request": f.name, "mode": mode, "completed": len(results), "errors": [],
            "collection": str(coll.relative_to(ROOT)),
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "engine": "public-youtube-transcript-fallback",
        }
        (STATE / f"{f.stem}.json").write_text(json.dumps(state, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        return True
    return False


def main():
    ok = True
    for f in sorted(REQ.glob("*.json")):
        try:
            if not process_file(f):
                ok = False
        except Exception as exc:
            print(f"PUBLIC FALLBACK REQUEST ERROR {f.name}: {exc}")
            ok = False
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
