#!/usr/bin/env python3
import html
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.request import Request, urlopen

ROOT = pathlib.Path(__file__).resolve().parent
REQ = ROOT / "requests"
OUT = ROOT / "transcripts"
COLL = ROOT / "collections"
STATE = ROOT / "processed"
WORK = ROOT / ".work"
for d in (OUT, COLL, STATE, WORK):
    d.mkdir(exist_ok=True)

SUPADATA_API_KEY = os.getenv("SUPADATA_API_KEY", "").strip()
YTDLP_PROXY = os.getenv("YTDLP_PROXY", "").strip()


def run(cmd, check=True):
    p = subprocess.run(cmd, text=True, capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}"
        )
    return p


def ytdlp(args, check=True):
    cmd = ["yt-dlp"]
    if YTDLP_PROXY:
        cmd.extend(["--proxy", YTDLP_PROXY])
    cmd.extend(args)
    return run(cmd, check=check)


def safe_name(text, max_len=80):
    text = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+", "_", str(text)).strip("._-")
    return (text or "video")[:max_len]


def source_id_from_url(url):
    try:
        u = urlparse(url)
        host = u.netloc.lower()
        if "youtube.com" in host:
            vid = parse_qs(u.query).get("v", [None])[0]
            if not vid:
                parts = [p for p in u.path.split("/") if p]
                if len(parts) >= 2 and parts[0] in {"shorts", "live", "embed"}:
                    vid = parts[1]
            return "youtube", vid
        if "youtu.be" in host:
            return "youtube", u.path.strip("/").split("/")[0]
        m = re.search(r"video(-?\d+_\d+)", url)
        if "vk" in host and m:
            return "vk", m.group(1)
        if "rutube" in host:
            parts = [p for p in u.path.split("/") if p]
            return "rutube", parts[-1] if parts else None
    except Exception:
        pass
    return "video", None


def clean_vtt(text):
    lines = []
    seen = set()
    for raw in text.splitlines():
        s = raw.strip()
        if (
            not s
            or s.startswith("WEBVTT")
            or s.startswith("Kind:")
            or s.startswith("Language:")
            or "-->" in s
            or re.fullmatch(r"\d+", s)
        ):
            continue
        s = re.sub(r"<[^>]+>", "", s)
        s = html.unescape(s)
        s = re.sub(r"\s+", " ", s).strip()
        if s and s not in seen:
            lines.append(s)
            seen.add(s)
    return "\n".join(lines)


def fmt_ms(ms):
    sec = max(0, int(ms or 0) // 1000)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def supadata_get(path, params):
    if not SUPADATA_API_KEY:
        raise RuntimeError("SUPADATA_API_KEY is not configured")
    url = "https://api.supadata.ai" + path + "?" + urlencode(params, doseq=True)
    req = Request(url, headers={"x-api-key": SUPADATA_API_KEY, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=120) as r:
            body = r.read().decode("utf-8")
            return json.loads(body)
    except Exception as exc:
        raise RuntimeError(f"Supadata request failed: {exc}") from exc


def supadata_search(query, limit=15, options=None):
    options = options or {}
    params = {
        "query": query,
        "type": "video",
        "limit": max(1, min(int(limit), 100)),
        "sortBy": options.get("sort_by", "relevance"),
    }
    if options.get("upload_date"):
        params["uploadDate"] = options["upload_date"]
    if options.get("duration_filter"):
        params["duration"] = options["duration_filter"]
    for feature in options.get("features", []) or []:
        params.setdefault("features", []).append(feature)
    data = supadata_get("/v1/youtube/search", params)
    items = []
    for r in data.get("results") or []:
        if r.get("type") != "video" or not r.get("id"):
            continue
        channel = r.get("channel") or {}
        items.append({
            "url": f"https://www.youtube.com/watch?v={r['id']}",
            "video_id": r.get("id"),
            "title": r.get("title") or r.get("id"),
            "uploader": channel.get("name") or "",
            "duration": r.get("duration"),
            "source": "youtube",
        })
    return items


def ytdlp_search(query, limit=15):
    limit = max(1, min(int(limit), 50))
    target = f"ytsearch{limit}:{query}"
    p = ytdlp(["--flat-playlist", "--dump-single-json", "--no-warnings", target])
    data = json.loads(p.stdout)
    items = []
    for e in (data.get("entries") or [])[:limit]:
        url = e.get("webpage_url") or e.get("original_url") or e.get("url")
        if not (url and str(url).startswith("http")) and e.get("id"):
            url = f"https://www.youtube.com/watch?v={e['id']}"
        if url:
            items.append({"url": str(url), "video_id": e.get("id"), "source": "youtube"})
    return items


def youtube_search(query, limit=15, options=None):
    if SUPADATA_API_KEY:
        return supadata_search(query, limit, options)
    return ytdlp_search(query, limit)


def dedupe_items(items):
    seen = set()
    out = []
    for item in items:
        url = item["url"]
        if url not in seen:
            seen.add(url)
            out.append(item)
    return out


def expand_request(data):
    if data.get("url"):
        return [{"url": data["url"]}], "single"
    if data.get("urls"):
        return dedupe_items([{"url": u} for u in data["urls"]]), "batch"
    if data.get("search"):
        return youtube_search(data["search"], data.get("limit", 15), data), "search"
    if data.get("searches"):
        items = []
        for item in data["searches"]:
            if isinstance(item, str):
                items.extend(youtube_search(item, 15, data))
            else:
                merged = dict(data)
                merged.update(item)
                items.extend(youtube_search(item["query"], item.get("limit", 15), merged))
        return dedupe_items(items), "search"
    raise ValueError("Request must contain url, urls, search, or searches")


def ensure_whisper():
    if shutil.which("whisper"):
        return
    print("No usable subtitles; installing Whisper fallback...")
    run([sys.executable, "-m", "pip", "install", "openai-whisper"])
    if not shutil.which("whisper"):
        raise RuntimeError("Whisper installation completed but CLI was not found")


def try_subtitles(url, work, sub_langs):
    for i, langs in enumerate(sub_langs, start=1):
        for old in work.glob(f"subs_{i}*.vtt"):
            old.unlink(missing_ok=True)
        tpl = str(work / f"subs_{i}.%(ext)s")
        ytdlp([
            "--skip-download", "--write-subs", "--write-auto-subs",
            "--sub-langs", langs, "--sub-format", "vtt", "-o", tpl, url
        ], check=False)
        vtts = sorted(work.glob(f"subs_{i}*.vtt"))
        if vtts:
            text = clean_vtt(vtts[0].read_text(encoding="utf-8", errors="ignore"))
            if text.strip():
                return text, f"subtitles ({langs})"
    return "", None


def supadata_transcribe(item, options):
    url = item["url"]
    params = {"url": url}
    params["lang"] = options.get("language", "ru")
    data = supadata_get("/v1/transcript", params)
    content = data.get("content")
    if isinstance(content, str):
        transcript = content.strip()
    elif isinstance(content, list):
        lines = []
        for seg in content:
            text = re.sub(r"\s+", " ", str(seg.get("text", ""))).strip()
            if text:
                lines.append(f"[{fmt_ms(seg.get('offset', seg.get('start', 0)))}] {text}")
        transcript = "\n".join(lines)
    else:
        transcript = ""
    if not transcript:
        raise RuntimeError("Supadata returned an empty transcript")

    title = item.get("title")
    uploader = item.get("uploader", "")
    duration = item.get("duration")
    video_id = item.get("video_id") or source_id_from_url(url)[1]

    if not title:
        try:
            meta = supadata_get("/v1/metadata", {"url": url})
            title = meta.get("title") or video_id or "YouTube video"
            duration = duration or meta.get("duration")
            channel = meta.get("channel") or {}
            uploader = uploader or channel.get("name") or meta.get("author") or ""
        except Exception:
            title = video_id or "YouTube video"

    return {
        "transcript": transcript,
        "title": title,
        "uploader": uploader,
        "duration": duration,
        "video_id": video_id,
        "webpage_url": url,
        "method": f"Supadata transcript ({data.get('lang') or 'auto'})",
    }


def ytdlp_transcribe(item, slug, options):
    url = item["url"]
    work = WORK / slug
    work.mkdir(parents=True, exist_ok=True)

    meta = ytdlp(["--dump-single-json", "--no-warnings", url]).stdout
    info = json.loads(meta)
    title = item.get("title") or info.get("title") or slug
    uploader = item.get("uploader") or info.get("uploader") or info.get("channel") or ""
    duration = item.get("duration") or info.get("duration")
    video_id = item.get("video_id") or info.get("id")
    webpage_url = info.get("webpage_url") or url

    sub_langs = options.get("sub_langs") or ["ru.*,ru", "en.*,en"]
    if isinstance(sub_langs, str):
        sub_langs = [sub_langs]
    transcript, method = try_subtitles(url, work, sub_langs)

    if not transcript.strip():
        method = "whisper"
        ensure_whisper()
        ytdlp([
            "-x", "--audio-format", "mp3", "--audio-quality", "5",
            "-o", str(work / "audio.%(ext)s"), url
        ])
        candidates = sorted(work.glob("audio.*"))
        if not candidates:
            raise RuntimeError("audio download produced no file")
        cmd = [
            "whisper", str(candidates[0]), "--model", options.get("whisper_model", "small"),
            "--task", "transcribe", "--output_format", "txt", "--output_dir", str(work)
        ]
        if options.get("whisper_language"):
            cmd.extend(["--language", options["whisper_language"]])
        run(cmd)
        txts = sorted(work.glob("*.txt"))
        if not txts:
            raise RuntimeError("whisper produced no txt")
        transcript = txts[-1].read_text(encoding="utf-8", errors="ignore")

    return {
        "transcript": transcript,
        "title": title,
        "uploader": uploader,
        "duration": duration,
        "video_id": video_id,
        "webpage_url": webpage_url,
        "method": method,
    }


def transcribe(item, slug, options=None):
    options = options or {}
    url = item["url"]
    out = OUT / f"{slug}.md"
    if out.exists() and out.stat().st_size > 100:
        print(f"Skip existing transcript: {out.name}")
        return {
            "path": str(out.relative_to(ROOT)), "url": url,
            "title": item.get("title") or url, "status": "existing"
        }

    source, _ = source_id_from_url(url)
    if source == "youtube" and SUPADATA_API_KEY:
        info = supadata_transcribe(item, options)
    else:
        info = ytdlp_transcribe(item, slug, options)

    md = [f"# {info['title']}", "", f"- Source: {info['webpage_url']}"]
    if info.get("uploader"):
        md.append(f"- Author/channel: {info['uploader']}")
    if info.get("video_id"):
        md.append(f"- Video ID: {info['video_id']}")
    if info.get("duration"):
        d = int(info["duration"])
        md.append(f"- Duration: {d//60}:{d%60:02d}")
    md.extend([
        f"- Transcript method: {info['method']}",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        "", "## Transcript", "", info["transcript"].strip(), ""
    ])
    out.write_text("\n".join(md), encoding="utf-8")
    return {
        "path": str(out.relative_to(ROOT)),
        "url": info["webpage_url"],
        "title": info["title"],
        "uploader": info.get("uploader", ""),
        "duration": info.get("duration"),
        "method": info["method"],
        "status": "created",
    }


def make_slug(request_stem, index, item, total):
    if total == 1:
        return request_stem
    source, sid = source_id_from_url(item["url"])
    sid = item.get("video_id") or sid
    tail = safe_name(sid or f"item_{index}")
    return f"{request_stem}__{index:02d}_{source}_{tail}"


def write_collection(request_stem, mode, data, results, errors):
    path = COLL / f"{request_stem}.md"
    lines = [f"# Collection: {request_stem}", "", f"- Mode: {mode}"]
    if data.get("search"):
        lines.append(f"- Search: {data['search']}")
    lines.extend([
        f"- Requested videos: {len(results) + len(errors)}",
        f"- Completed: {len(results)}",
        f"- Errors: {len(errors)}",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        "", "## Videos", ""
    ])
    for i, r in enumerate(results, 1):
        dur = r.get("duration")
        dur_txt = f" — {int(dur)//60}:{int(dur)%60:02d}" if dur else ""
        lines.append(f"{i}. **{r.get('title') or r['url']}**{dur_txt}")
        lines.append(f"   - Source: {r['url']}")
        lines.append(f"   - Transcript: `{r['path']}`")
        if r.get("method"):
            lines.append(f"   - Method: {r['method']}")
    if errors:
        lines.extend(["", "## Errors", ""])
        for e in errors:
            lines.append(f"- {e['url']}: {e['error']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def process_request(f):
    done = STATE / f"{f.stem}.json"
    if done.exists():
        print(f"Skip processed request: {f.name}")
        return True

    data = json.loads(f.read_text(encoding="utf-8"))
    items, mode = expand_request(data)
    if not items:
        raise RuntimeError("Request expanded to zero video URLs")

    results = []
    errors = []
    total = len(items)
    for i, item in enumerate(items, 1):
        url = item["url"]
        slug = make_slug(f.stem, i, item, total)
        print(f"[{i}/{total}] Transcribing {url}")
        try:
            results.append(transcribe(item, slug, data))
        except Exception as exc:
            print(f"ERROR {url}: {exc}", file=sys.stderr)
            errors.append({"url": url, "error": str(exc)[:2000]})

    collection = write_collection(f.stem, mode, data, results, errors)
    state = {
        "request": f.name,
        "mode": mode,
        "completed": len(results),
        "errors": errors,
        "collection": str(collection.relative_to(ROOT)),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    if not errors:
        done.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    return False


def main():
    files = sorted(REQ.glob("*.json"))
    if not files:
        print("No request files")
        return
    print(f"YouTube engine: {'Supadata' if SUPADATA_API_KEY else 'yt-dlp'}")
    ok = True
    for f in files:
        try:
            if not process_request(f):
                ok = False
        except Exception as exc:
            ok = False
            print(f"REQUEST ERROR {f.name}: {exc}", file=sys.stderr)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
