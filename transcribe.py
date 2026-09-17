#!/usr/bin/env python3
import html
import json
import pathlib
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

ROOT = pathlib.Path(__file__).resolve().parent
REQ = ROOT / "requests"
OUT = ROOT / "transcripts"
COLL = ROOT / "collections"
STATE = ROOT / "processed"
WORK = ROOT / ".work"
for d in (OUT, COLL, STATE, WORK):
    d.mkdir(exist_ok=True)


def run(cmd, check=True):
    p = subprocess.run(cmd, text=True, capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}"
        )
    return p


def safe_name(text, max_len=80):
    text = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+", "_", str(text)).strip("._-")
    return (text or "video")[:max_len]


def source_id_from_url(url):
    try:
        u = urlparse(url)
        host = u.netloc.lower()
        if "youtube.com" in host:
            vid = parse_qs(u.query).get("v", [None])[0]
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


def youtube_search(query, limit=15):
    limit = max(1, min(int(limit), 50))
    target = f"ytsearch{limit}:{query}"
    p = run([
        "yt-dlp", "--flat-playlist", "--dump-single-json", "--no-warnings", target
    ])
    data = json.loads(p.stdout)
    urls = []
    for e in (data.get("entries") or [])[:limit]:
        url = e.get("webpage_url") or e.get("original_url") or e.get("url")
        if url and str(url).startswith("http"):
            urls.append(str(url))
        elif e.get("id"):
            urls.append(f"https://www.youtube.com/watch?v={e['id']}")
    return list(dict.fromkeys(urls))


def expand_request(data):
    if data.get("url"):
        return [data["url"]], "single"
    if data.get("urls"):
        return list(dict.fromkeys(data["urls"])), "batch"
    if data.get("search"):
        return youtube_search(data["search"], data.get("limit", 15)), "search"
    if data.get("searches"):
        urls = []
        for item in data["searches"]:
            if isinstance(item, str):
                urls.extend(youtube_search(item, 15))
            else:
                urls.extend(youtube_search(item["query"], item.get("limit", 15)))
        return list(dict.fromkeys(urls)), "search"
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
        run([
            "yt-dlp", "--skip-download", "--write-subs", "--write-auto-subs",
            "--sub-langs", langs, "--sub-format", "vtt", "-o", tpl, url
        ], check=False)
        vtts = sorted(work.glob(f"subs_{i}*.vtt"))
        if vtts:
            text = clean_vtt(vtts[0].read_text(encoding="utf-8", errors="ignore"))
            if text.strip():
                return text, f"subtitles ({langs})"
    return "", None


def transcribe(url, slug, options=None):
    options = options or {}
    out = OUT / f"{slug}.md"
    if out.exists() and out.stat().st_size > 100:
        print(f"Skip existing transcript: {out.name}")
        return {"path": str(out.relative_to(ROOT)), "url": url, "status": "existing"}

    work = WORK / slug
    work.mkdir(parents=True, exist_ok=True)

    meta = run(["yt-dlp", "--dump-single-json", "--no-warnings", url]).stdout
    info = json.loads(meta)
    title = info.get("title") or slug
    uploader = info.get("uploader") or info.get("channel") or ""
    duration = info.get("duration")
    video_id = info.get("id")
    webpage_url = info.get("webpage_url") or url

    sub_langs = options.get("sub_langs") or ["ru.*,ru", "en.*,en"]
    if isinstance(sub_langs, str):
        sub_langs = [sub_langs]

    transcript, method = try_subtitles(url, work, sub_langs)

    if not transcript.strip():
        method = "whisper"
        ensure_whisper()
        run([
            "yt-dlp", "-x", "--audio-format", "mp3", "--audio-quality", "5",
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

    md = [f"# {title}", "", f"- Source: {webpage_url}"]
    if webpage_url != url:
        md.append(f"- Requested URL: {url}")
    if uploader:
        md.append(f"- Author/channel: {uploader}")
    if video_id:
        md.append(f"- Video ID: {video_id}")
    if duration:
        md.append(f"- Duration: {int(duration)//60}:{int(duration)%60:02d}")
    md.extend([
        f"- Transcript method: {method}",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        "", "## Transcript", "", transcript.strip(), ""
    ])
    out.write_text("\n".join(md), encoding="utf-8")
    return {
        "path": str(out.relative_to(ROOT)),
        "url": webpage_url,
        "title": title,
        "uploader": uploader,
        "duration": duration,
        "method": method,
        "status": "created",
    }


def make_slug(request_stem, index, url, total):
    if total == 1:
        return request_stem
    source, sid = source_id_from_url(url)
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
    urls, mode = expand_request(data)
    if not urls:
        raise RuntimeError("Request expanded to zero video URLs")

    results = []
    errors = []
    total = len(urls)
    for i, url in enumerate(urls, 1):
        slug = make_slug(f.stem, i, url, total)
        print(f"[{i}/{total}] Transcribing {url}")
        try:
            results.append(transcribe(url, slug, data))
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
