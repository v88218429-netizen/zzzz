#!/usr/bin/env python3
import json, pathlib, re, subprocess, time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = pathlib.Path(__file__).resolve().parent
REQ = ROOT / "monitor_requests"
OUT = ROOT / "transcripts"
BASE = ROOT / "monitor_tasks"
RES = BASE / "results"
STATE = BASE / "state"
DONE = BASE / "processed"
for d in (REQ, OUT, RES, STATE, DONE):
    d.mkdir(parents=True, exist_ok=True)

RATE_LIMIT_MARKERS = ("high volume","higher rate limits","commercial / partnership","email will@")

def run(cmd):
    p = subprocess.run(cmd, text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip() or "command failed")
    return p.stdout

def safe(s):
    return re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+","_",str(s)).strip("._-")[:80] or "task"

def yt_search(query, limit):
    errs=[]
    for prefix in ("ytsearchdate","ytsearch"):
        try:
            raw=run(["yt-dlp","--flat-playlist","--dump-single-json","--no-warnings",f"{prefix}{min(max(int(limit),1),50)}:{query}"])
            data=json.loads(raw)
            items=[]
            for e in data.get("entries") or []:
                vid=e.get("id")
                if not vid: continue
                items.append({
                    "video_id":vid,
                    "url":f"https://www.youtube.com/watch?v={vid}",
                    "title":e.get("title") or vid,
                    "channel":e.get("uploader") or e.get("channel") or "",
                    "duration":e.get("duration"),
                    "timestamp":e.get("timestamp") or e.get("release_timestamp"),
                    "query":query
                })
            if items: return items
        except Exception as exc:
            errs.append(f"{prefix}: {exc}")
    raise RuntimeError(" | ".join(errs) or "no results")

def kind(item):
    try:
        if item.get("duration") is not None and int(item["duration"]) <= 180:
            return "short"
    except Exception:
        pass
    t=(item.get("title") or "").lower()
    if "#shorts" in t or "#short" in t:
        return "short"
    return "video"

def fetch_text(url, timeout=90):
    req=Request(url, headers={"User-Agent":"Mozilla/5.0","Accept":"text/plain,text/markdown,application/json,*/*"})
    with urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", errors="replace")

def transcript_ai(vid, lang="ru"):
    last=None
    for url in (f"https://youtube-transcript.ai/transcript/{vid}.txt?lang={lang}",f"https://youtube-transcript.ai/transcript/{vid}.txt"):
        try:
            status,text=fetch_text(url)
            low=text.lower()
            if status!=200 or any(x in low for x in RATE_LIMIT_MARKERS):
                continue
            m=re.search(r"(?m)^\[\d{1,2}:\d{2}(?::\d{2})?\]",text)
            if not m: continue
            return text[m.start():].strip(), "youtube-transcript.ai"
        except Exception as exc:
            last=exc
    if last: raise last
    raise RuntimeError("no valid transcript")

def free_api(vid, lang="ru"):
    status,text=fetch_text("https://api.freetranscriptapi.com/v1/transcript?"+urlencode({"video_url":vid,"lang":lang}))
    if status!=200: raise RuntimeError(f"HTTP {status}")
    data=json.loads(text)
    rows=data.get("transcript") or data.get("segments") or []
    lines=[]
    for row in rows:
        body=re.sub(r"\s+"," ",str(row.get("text",""))).strip()
        if not body: continue
        try: sec=int(float(row.get("start",0) or 0))
        except Exception: sec=0
        h,rem=divmod(sec,3600); m,s=divmod(rem,60)
        stamp=f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        lines.append(f"[{stamp}] {body}")
    if not lines: raise RuntimeError(data.get("error") or "empty transcript")
    return "\n".join(lines), "FreeTranscriptAPI"

def get_transcript(vid, lang="ru"):
    errs=[]
    for fn in (transcript_ai, free_api):
        try: return fn(vid, lang)
        except Exception as exc: errs.append(f"{fn.__name__}: {exc}")
    raise RuntimeError(" | ".join(errs))

def load_seen(task_id):
    p=STATE/f"{safe(task_id)}.json"
    if not p.exists(): return set(), p
    try: return set(json.loads(p.read_text(encoding="utf-8")).get("seen",[])), p
    except Exception: return set(), p

def fmt_dur(v):
    try: sec=int(v)
    except Exception: return ""
    m,s=divmod(sec,60); h,m=divmod(m,60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def process_request(path):
    marker=DONE/f"{path.stem}.json"
    if marker.exists(): return
    cfg=json.loads(path.read_text(encoding="utf-8"))
    task_id=cfg["task_id"]
    lang=cfg.get("language","ru")
    base_queries=[cfg.get("query","")] + list(cfg.get("extra_queries") or [])
    base_queries=[q.strip() for q in base_queries if q and q.strip()]
    content=(cfg.get("content") or "Оба").lower()
    limit_video=int(cfg.get("limit_video",15) or 15)
    limit_shorts=int(cfg.get("limit_shorts",25) or 25)
    depth=int(cfg.get("depth_days",7) or 7)
    seen,state_path=load_seen(task_id)

    searches=[]
    for q in base_queries:
        if content in ("оба","видео","video","all"):
            searches.append((q,max(limit_video*3,20)))
        if content in ("оба","shorts","short","all"):
            searches.append((q+" shorts",max(limit_shorts*3,25)))

    found={}
    for q,lim in searches:
        try:
            for item in yt_search(q,lim):
                found.setdefault(item["video_id"],item)
        except Exception as exc:
            print(f"SEARCH ERROR {q}: {exc}")

    cutoff=datetime.now(timezone.utc)-timedelta(days=depth)
    candidates=[]
    for item in found.values():
        if item["video_id"] in seen: continue
        ts=item.get("timestamp")
        if ts:
            try:
                if datetime.fromtimestamp(int(ts),timezone.utc) < cutoff: continue
            except Exception: pass
        candidates.append(item)

    candidates.sort(key=lambda x: int(x.get("timestamp") or 0), reverse=True)
    picked=[]; vc=sc=0
    for item in candidates:
        k=kind(item)
        if k=="video" and content not in ("shorts","short") and vc<limit_video:
            picked.append(item); vc+=1
        elif k=="short" and content not in ("видео","video") and sc<limit_shorts:
            picked.append(item); sc+=1
        if vc>=limit_video and sc>=limit_shorts: break

    day=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results=[]; errors=[]
    for item in picked:
        vid=item["video_id"]
        try:
            text,method=get_transcript(vid,lang)
            k=kind(item)
            fname=f"{day}_{safe(task_id)}_{k}_{vid}.md"
            tp=OUT/fname
            md=[
                f"# {item['title']}","",
                f"- Source: {item['url']}",
                f"- Video ID: {vid}",
                f"- Task ID: {task_id}",
                f"- Content type: {k}",
                f"- Author/channel: {item.get('channel','')}",
                f"- Duration: {fmt_dur(item.get('duration'))}",
                f"- Discovery query: {item.get('query','')}",
                f"- Transcript method: {method}",
                f"- Generated: {datetime.now(timezone.utc).isoformat()}",
                "","## Transcript","",text.strip(),""
            ]
            tp.write_text("\n".join(md),encoding="utf-8")
            results.append({**item,"kind":k,"transcript":str(tp.relative_to(ROOT)),"method":method})
            seen.add(vid)
        except Exception as exc:
            errors.append({"video_id":vid,"url":item["url"],"title":item["title"],"error":str(exc)[:800]})
        time.sleep(0.15)

    state_path.write_text(json.dumps({"task_id":task_id,"seen":sorted(seen),"updated_at":datetime.now(timezone.utc).isoformat()},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    payload={
        "task_id":task_id,
        "request":path.name,
        "query":cfg.get("query"),
        "knowledge_tag":cfg.get("knowledge_tag",""),
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "videos":results,
        "errors":errors
    }
    out=RES/f"{path.stem}.json"
    out.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    marker.write_text(json.dumps({"result":str(out.relative_to(ROOT)),"processed_at":datetime.now(timezone.utc).isoformat()},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def main():
    files=sorted(REQ.glob("*.json"))
    for f in files:
        try: process_request(f)
        except Exception as exc: print(f"REQUEST ERROR {f.name}: {exc}")

if __name__=="__main__":
    main()
