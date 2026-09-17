#!/usr/bin/env python3
import html
import json
import pathlib
import re
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = pathlib.Path(__file__).resolve().parent
TRANSCRIPTS = ROOT / "transcripts"
MONITOR = ROOT / "monitor"
DAILY = MONITOR / "daily"
STATE_FILE = MONITOR / "state.json"
LATEST = MONITOR / "latest.md"
for d in (TRANSCRIPTS, MONITOR, DAILY):
    d.mkdir(parents=True, exist_ok=True)

SEARCHES = [
    {"query": "реклама Wildberries 2026", "limit": 35, "kind": "all"},
    {"query": "реклама WB 2026", "limit": 25, "kind": "all"},
    {"query": "Wildberries реклама 2026 #shorts", "limit": 30, "kind": "shorts"},
    {"query": "WB реклама 2026 #shorts", "limit": 30, "kind": "shorts"},
    {"query": "Wildberries продвижение 2026 shorts", "limit": 25, "kind": "shorts"},
]

WB_HINTS = ("wildberries", "вайлдбер", "вайлдберриз", " wb ", " вб ", "wb ", "вб ")
SELLER_HINTS = (
    "реклам", "продвиж", "ставк", "cpc", "cpm", "дрр", "drr", "ctr", "cpo",
    "кластер", "полк", "охват", "поиск", "кабинет", "селлер", "карточк", "товар",
    "конверс", "воронк", "аукцион", "органик", "маржин", "прибыл", "биддер",
)
EDU_HINTS = (
    "как ", "как?", "настро", "разбор", "инструк", "гайд", "ошиб", "что измен",
    "новый", "новая", "обнов", "почему", "схема", "стратег", "лайфхак", "кейс",
)
CHANGE_HINTS = (
    "теперь", "измен", "обнов", "добав", "убрал", "убрали", "появ", "новая логика",
    "новый алгоритм", "новые правила", "минимальная ставка", "охват",
)
VALUE_HINTS = (
    "cpc", "cpm", "дрр", "drr", "ctr", "cpo", "ставк", "кластер", "полк", "охват",
    "поиск", "позици", "конверс", "корзин", "заказ", "органик", "расход", "бюджет",
    "прибыл", "маржин", "алгоритм", "показ", "клик", "реклам",
)
RATE_LIMIT_MARKERS = (
    "high volume", "higher rate limits", "commercial / partnership", "email will@",
)


def run(cmd):
    p = subprocess.run(cmd, text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip() or "command failed")
    return p.stdout


def yt_search(query, limit):
    # ytsearchdate normally gives fresh-first results. Fall back to regular relevance search.
    errors = []
    for prefix in ("ytsearchdate", "ytsearch"):
        target = f"{prefix}{max(1, min(int(limit), 50))}:{query}"
        try:
            raw = run(["yt-dlp", "--flat-playlist", "--dump-single-json", "--no-warnings", target])
            data = json.loads(raw)
            items = []
            for e in (data.get("entries") or [])[:limit]:
                vid = e.get("id")
                if not vid:
                    continue
                items.append({
                    "video_id": vid,
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "title": e.get("title") or vid,
                    "uploader": e.get("uploader") or e.get("channel") or "",
                    "duration": e.get("duration"),
                    "timestamp": e.get("timestamp") or e.get("release_timestamp"),
                    "source_query": query,
                })
            if items:
                return items
        except Exception as exc:
            errors.append(f"{prefix}: {exc}")
    raise RuntimeError(" | ".join(errors) or f"No search results for {query}")


def looks_relevant(title):
    t = f" {str(title).lower()} "
    wb = any(x in t for x in WB_HINTS)
    seller_score = sum(1 for x in SELLER_HINTS if x in t)
    edu = any(x in t for x in EDU_HINTS)
    # Brand commercials usually contain only 'реклама Wildberries' and no seller vocabulary.
    return wb and seller_score >= 1 and (edu or seller_score >= 2)


def scan_existing_video_ids():
    ids = set()
    pat = re.compile(r"^- Video ID:\s*([A-Za-z0-9_-]{6,})\s*$", re.M)
    for path in TRANSCRIPTS.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")[:5000]
            m = pat.search(text)
            if m:
                ids.add(m.group(1))
        except Exception:
            pass
    return ids


def load_state():
    if not STATE_FILE.exists():
        return {"initialized": False, "seen": {}, "ignored": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        data.setdefault("initialized", True)
        data.setdefault("seen", {})
        data.setdefault("ignored", {})
        return data
    except Exception:
        return {"initialized": False, "seen": {}, "ignored": {}}


def save_state(state):
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_text(url, timeout=90):
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/plain,text/markdown,application/json,*/*",
    })
    with urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", errors="replace")


def transcript_ai(vid, lang="ru"):
    last = None
    for url in (
        f"https://youtube-transcript.ai/transcript/{vid}.txt?lang={lang}",
        f"https://youtube-transcript.ai/transcript/{vid}.txt",
    ):
        try:
            status, text = fetch_text(url)
            low = text.lower()
            if status != 200 or any(x in low for x in RATE_LIMIT_MARKERS):
                continue
            ts = re.search(r"(?m)^\[\d{1,2}:\d{2}(?::\d{2})?\]", text)
            if not ts:
                continue
            title = ""
            m = re.search(r"^# Transcript:\s*(.+)$", text, re.M)
            if m:
                title = m.group(1).strip()
            return {"text": text[ts.start():].strip(), "title": title, "method": "youtube-transcript.ai"}
        except Exception as exc:
            last = exc
    if last:
        raise last
    raise RuntimeError("youtube-transcript.ai returned no valid timestamped transcript")


def free_transcript_api(vid, lang="ru"):
    params = urlencode({"video_url": vid, "lang": lang})
    status, text = fetch_text(f"https://api.freetranscriptapi.com/v1/transcript?{params}")
    if status != 200:
        raise RuntimeError(f"FreeTranscriptAPI HTTP {status}")
    data = json.loads(text)
    rows = data.get("transcript") or data.get("segments") or []
    lines = []
    for row in rows:
        body = re.sub(r"\s+", " ", str(row.get("text", ""))).strip()
        if not body:
            continue
        try:
            sec = int(float(row.get("start", 0) or 0))
        except Exception:
            sec = 0
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        stamp = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        lines.append(f"[{stamp}] {body}")
    if not lines:
        raise RuntimeError(data.get("error") or "FreeTranscriptAPI returned no transcript")
    return {"text": "\n".join(lines), "title": data.get("title") or "", "method": "FreeTranscriptAPI"}


def get_transcript(vid):
    errors = []
    for fn in (transcript_ai, free_transcript_api):
        try:
            return fn(vid)
        except Exception as exc:
            errors.append(f"{fn.__name__}: {exc}")
    raise RuntimeError(" | ".join(errors))


def fmt_duration(duration):
    try:
        sec = int(duration)
    except Exception:
        return ""
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def content_type(item):
    try:
        if item.get("duration") is not None and int(item["duration"]) <= 180:
            return "short"
    except Exception:
        pass
    q = str(item.get("source_query", "")).lower()
    title = str(item.get("title", "")).lower()
    if "short" in q or "#short" in title:
        return "short"
    return "video"


def write_transcript(item, result, day):
    vid = item["video_id"]
    kind = content_type(item)
    path = TRANSCRIPTS / f"{day}_daily_wb_{kind}_{vid}.md"
    title = result.get("title") or item.get("title") or vid
    md = [
        f"# {title}", "",
        f"- Source: https://www.youtube.com/watch?v={vid}",
        f"- Video ID: {vid}",
        f"- Content type: {kind}",
    ]
    if item.get("uploader"):
        md.append(f"- Author/channel: {item['uploader']}")
    if item.get("duration") is not None:
        md.append(f"- Duration: {fmt_duration(item['duration'])}")
    md += [
        f"- Discovery query: {item.get('source_query', '')}",
        f"- Transcript method: {result['method']}",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        "", "## Transcript", "", result["text"].strip(), "",
    ]
    path.write_text("\n".join(md), encoding="utf-8")
    return path, title, kind


def collapse_repetition(text):
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"\s+", " ", text).strip()
    # youtube-transcript.ai occasionally repeats the same short phrase 2-3x in one cue.
    words = text.split()
    if len(words) >= 8:
        for size in range(min(30, len(words)//2), 2, -1):
            if len(words) >= size * 2 and words[:size] == words[size:size*2]:
                words = words[:size] + words[size*2:]
                break
    return " ".join(words)


def transcript_lines(text):
    rows = []
    for raw in text.splitlines():
        m = re.match(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.+)$", raw.strip())
        if not m:
            continue
        body = collapse_repetition(m.group(2))
        if len(body) >= 20:
            rows.append((m.group(1), body))
    return rows


def pick_highlights(text, limit):
    candidates = []
    for stamp, body in transcript_lines(text):
        low = body.lower()
        score = sum(2 for x in VALUE_HINTS if x in low)
        score += sum(2 for x in CHANGE_HINTS if x in low)
        if re.search(r"\d", body):
            score += 2
        if "%" in body or "руб" in low:
            score += 2
        if score <= 0:
            continue
        candidates.append((score, stamp, body))
    candidates.sort(key=lambda x: (-x[0], x[1]))
    out, seen = [], set()
    for score, stamp, body in candidates:
        key = re.sub(r"[^а-яa-z0-9]+", " ", body.lower())[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append((stamp, body))
        if len(out) >= limit:
            break
    return out


def pick_changes(text, limit=4):
    out, seen = [], set()
    for stamp, body in transcript_lines(text):
        low = body.lower()
        if not any(x in low for x in CHANGE_HINTS):
            continue
        key = re.sub(r"[^а-яa-z0-9]+", " ", low)[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append((stamp, body))
        if len(out) >= limit:
            break
    return out


def write_digest(day, processed, ignored, errors, bootstrap=False):
    path = DAILY / f"{day}.md"
    shorts = [x for x in processed if x["kind"] == "short"]
    videos = [x for x in processed if x["kind"] != "short"]
    lines = [
        f"# Daily WB video monitor — {day}", "",
        f"- New relevant: {len(processed)}",
        f"- Shorts: {len(shorts)}",
        f"- Full videos: {len(videos)}",
        f"- Ignored noise: {len(ignored)}",
        f"- Transcript errors: {len(errors)}",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
    ]
    if bootstrap:
        lines += ["", "> Baseline initialized. Existing search results were marked as already seen; future runs will process only newly appearing IDs."]
    for heading, items in (("Shorts", shorts), ("Full videos", videos)):
        lines += ["", f"## {heading}", ""]
        if not items:
            lines.append("No new items.")
            continue
        for x in items:
            lines += [
                f"### {x['title']}", "",
                f"- Source: {x['url']}",
                f"- Duration: {fmt_duration(x.get('duration')) or 'unknown'}",
                f"- Transcript: `{x['transcript']}`",
                f"- Method: {x['method']}",
            ]
            if x.get("changes"):
                lines += ["- Possible changes / updates:"]
                for stamp, body in x["changes"]:
                    lines.append(f"  - [{stamp}] {body}")
            if x.get("highlights"):
                lines += ["- High-signal fragments:"]
                for stamp, body in x["highlights"]:
                    lines.append(f"  - [{stamp}] {body}")
            lines.append("")
    if ignored:
        lines += ["## Ignored as likely non-seller / brand-ad noise", ""]
        for x in ignored[:30]:
            lines.append(f"- {x.get('title') or x['video_id']} — {x['url']}")
    if errors:
        lines += ["", "## Transcript errors", ""]
        for x in errors:
            lines.append(f"- {x['url']}: {x['error']}")
    text = "\n".join(lines).rstrip() + "\n"
    path.write_text(text, encoding="utf-8")
    LATEST.write_text(text, encoding="utf-8")
    return path


def main():
    day = datetime.now(timezone.utc).date().isoformat()
    state = load_state()
    existing = scan_existing_video_ids()
    known = set(state.get("seen", {})) | set(state.get("ignored", {})) | existing

    found = {}
    search_errors = []
    for spec in SEARCHES:
        try:
            rows = yt_search(spec["query"], spec["limit"])
            for item in rows:
                old = found.get(item["video_id"])
                if not old:
                    item["query_kind"] = spec["kind"]
                    found[item["video_id"]] = item
                elif spec["kind"] == "shorts":
                    old["query_kind"] = "shorts"
        except Exception as exc:
            search_errors.append({"url": spec["query"], "error": str(exc)[:1000]})

    current_ids = set(found)
    if not state.get("initialized"):
        # First scheduled run is a baseline, not a historical backfill.
        for vid, item in found.items():
            if looks_relevant(item.get("title", "")):
                state["seen"][vid] = {
                    "title": item.get("title", ""), "url": item["url"],
                    "status": "baseline", "first_seen": datetime.now(timezone.utc).isoformat(),
                }
            else:
                state["ignored"][vid] = {
                    "title": item.get("title", ""), "url": item["url"],
                    "status": "noise_baseline", "first_seen": datetime.now(timezone.utc).isoformat(),
                }
        state["initialized"] = True
        state["baseline_date"] = day
        save_state(state)
        write_digest(day, [], [], search_errors, bootstrap=True)
        print(f"Baseline initialized with {len(found)} search IDs; no historical backfill.")
        return

    new_items = [item for vid, item in found.items() if vid not in known]
    ignored, relevant = [], []
    for item in new_items:
        if looks_relevant(item.get("title", "")):
            relevant.append(item)
        else:
            ignored.append(item)
            state["ignored"][item["video_id"]] = {
                "title": item.get("title", ""), "url": item["url"],
                "status": "noise", "first_seen": datetime.now(timezone.utc).isoformat(),
            }

    # Keep daily public endpoints healthy if a large batch appears unexpectedly.
    relevant = relevant[:20]
    processed, transcript_errors = [], []
    for idx, item in enumerate(relevant, 1):
        try:
            print(f"[{idx}/{len(relevant)}] transcript {item['video_id']} {item['title']}")
            result = get_transcript(item["video_id"])
            path, title, kind = write_transcript(item, result, day)
            body = result["text"]
            entry = {
                **item,
                "title": title,
                "kind": kind,
                "transcript": str(path.relative_to(ROOT)),
                "method": result["method"],
                "highlights": pick_highlights(body, 5 if kind == "short" else 8),
                "changes": pick_changes(body, 4),
            }
            processed.append(entry)
            state["seen"][item["video_id"]] = {
                "title": title, "url": item["url"], "kind": kind,
                "status": "transcribed", "first_seen": datetime.now(timezone.utc).isoformat(),
                "transcript": str(path.relative_to(ROOT)),
            }
        except Exception as exc:
            transcript_errors.append({"url": item["url"], "error": str(exc)[:1200]})
        time.sleep(0.35)

    save_state(state)
    digest_errors = search_errors + transcript_errors
    digest = write_digest(day, processed, ignored, digest_errors)
    print(f"New candidates={len(new_items)} relevant={len(relevant)} processed={len(processed)} shorts={sum(1 for x in processed if x['kind']=='short')} digest={digest}")


if __name__ == "__main__":
    main()
