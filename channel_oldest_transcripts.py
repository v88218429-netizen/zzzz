#!/usr/bin/env python3
"""Transcribe the newest or oldest public long-form uploads of a YouTube channel."""
import json, pathlib, re, subprocess, datetime, time, sys
from urllib.request import Request, urlopen
ROOT=pathlib.Path(__file__).resolve().parent
REQ=ROOT/"channel_requests"
OUT=ROOT/"transcripts"
REPORT=ROOT/"monitor_tasks"/"results"
for p in (OUT,REPORT):p.mkdir(parents=True,exist_ok=True)
def cmd(args):
 p=subprocess.run(args,text=True,capture_output=True,timeout=1800)
 if p.returncode:raise RuntimeError((p.stderr or p.stdout)[-1500:])
 return p.stdout
def transcript(vid):
 errors=[]
 for url in (f"https://youtube-transcript.ai/transcript/{vid}.txt?lang=ru",f"https://youtube-transcript.ai/transcript/{vid}.txt"):
  try:
   with urlopen(Request(url,headers={"User-Agent":"Mozilla/5.0"}),timeout=90) as r:text=r.read().decode("utf-8","replace")
   match=re.search(r"(?m)^\[\d{1,2}:\d{2}(?::\d{2})?\]",text)
   if match and "higher rate limits" not in text.lower():return text[match.start():].strip(),"youtube-transcript.ai"
  except Exception as exc:errors.append(str(exc))
 try:
  from video_task_monitor import free_api
  return free_api(vid,"ru")
 except Exception as exc:errors.append(str(exc))
 raise RuntimeError(" | ".join(errors) or "No valid caption transcript")
def fmt_duration(seconds):
 try: seconds=int(float(seconds))
 except Exception: return ""
 hours,rem=divmod(seconds,3600); minutes,seconds=divmod(rem,60)
 return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
def timestamp_seconds(stamp):
 parts=[int(x) for x in stamp.split(":")]
 if len(parts)==3:return parts[0]*3600+parts[1]*60+parts[2]
 if len(parts)==2:return parts[0]*60+parts[1]
 return None
def verify_transcript(path,duration):
 text=path.read_text(encoding="utf-8")
 body=text.split("## Transcript",1)[-1].strip()
 stamps=re.findall(r"(?m)^\[(\d{1,2}:\d{2}(?::\d{2})?)\]",body)
 seconds=[timestamp_seconds(x) for x in stamps]
 seconds=[x for x in seconds if x is not None]
 coverage=None
 try:
  if duration and seconds: coverage=round(min(1,seconds[-1]/float(duration))*100,1)
 except Exception: pass
 words=re.findall(r"[А-Яа-яЁёA-Za-z0-9]+",body)
 lines=[re.sub(r"\s+"," ",line.strip().lower()) for line in body.splitlines() if line.strip()]
 unique=len(set(lines))
 duplicate_ratio=1-(unique/max(1,len(lines)))
 if not body or len(words)<100 or not seconds:
  return "PARTIAL_TRANSCRIPT",coverage,"missing transcript text or timestamps"
 if duration and coverage is not None and coverage<90:
  return "PARTIAL_TRANSCRIPT",coverage,"last timestamp covers less than 90% of video duration"
 if duplicate_ratio>0.35:
  return "PARTIAL_TRANSCRIPT",coverage,"high proportion of repeated transcript lines"
 return "COMPLETE_VERIFIED",coverage,"timestamps and transcript content present; end coverage checked"
def main():
 failed=False
 for req in sorted(REQ.glob("*.json")):
  config=json.loads(req.read_text())
  task=config["task_id"]; channel=config["channel_url"].rstrip("/")
  fixed_ids=config.get("video_ids")
  count=len(fixed_ids) if fixed_ids else int(config.get("count",15))
  selection=config.get("selection","newest_public_long_form_videos")
  if selection not in ("newest_public_long_form_videos","oldest_public_long_form_videos","fixed_video_ids"):
   raise ValueError(f"Unsupported selection: {selection}")
  if fixed_ids and len(fixed_ids)!=len(set(fixed_ids)):
   raise ValueError("video_ids contains duplicates")
  done=REPORT/(req.stem+".json")
  if done.exists() and json.loads(done.read_text()).get("completed"):continue
  result={"task_id":task,"channel":channel,"selection":selection,"requested":count,"videos":[],"errors":[],"completed":False}
  try:
   raw=cmd(["yt-dlp","--flat-playlist","--dump-single-json","--no-warnings",channel+"/videos"])
   entries=[e for e in json.loads(raw).get("entries",[]) if e and e.get("id") and e.get("id")!="NA"]
   entries=[e for e in entries if not (e.get("duration") is not None and float(e["duration"])<=180)]
   if fixed_ids:
    by_id={e["id"]:e for e in entries}
    selected=[]
    for vid in fixed_ids:
     e=by_id.get(vid)
     if not e:
      try:
       detail=json.loads(cmd(["yt-dlp","--skip-download","--dump-single-json","--no-warnings",f"https://www.youtube.com/watch?v={vid}"]))
       if detail.get("id")!=vid:raise RuntimeError("YouTube metadata ID mismatch")
       e=detail
      except Exception as exc:
       result["errors"].append({"id":vid,"stage":"metadata","error":str(exc)[:500]})
       continue
     if e.get("duration") is not None and float(e["duration"])<=180:
      result["errors"].append({"id":vid,"stage":"selection","error":"Video is 180 seconds or shorter; excluded as a short clip"})
      continue
     selected.append(e)
   else:
    if len(entries)<count:raise RuntimeError(f"Only {len(entries)} long-video entries retrieved; expected {count}")
    selected=entries[:count] if selection.startswith("newest") else list(reversed(entries[-count:]))
   for idx,e in enumerate(selected,1):
    vid=e["id"]; target=OUT/f"{task}_{idx:02d}_{vid}.md"; method=""
    try:
     if target.exists() and "## Transcript" in target.read_text(encoding="utf-8"):
      method="existing"
     else:
      body,method=transcript(vid)
      if not body.strip():raise RuntimeError("Empty transcript")
      published=e.get("upload_date") or e.get("release_date") or e.get("timestamp") or ""
      if len(str(published))==8: published=f"{str(published)[:4]}-{str(published)[4:6]}-{str(published)[6:]}"
      duration=e.get("duration")
      md=f"# {e.get('title',vid)}\n\n- Channel: Михаил Дашкиев\n- Channel URL: {channel}\n- Video URL: https://www.youtube.com/watch?v={vid}\n- Video ID: {vid}\n- Published: {published}\n- Duration: {fmt_duration(duration)}\n- Transcript method: {method}\n- Processing status: pending verification\n\n## Transcript\n\n{body.strip()}\n"
      target.write_text(md,encoding="utf-8")
     status,coverage,reason=verify_transcript(target,e.get("duration"))
     current=target.read_text(encoding="utf-8")
     current=current.replace("- Processing status: pending verification",f"- Processing status: {status}\n- Timestamp coverage: {coverage}%\n- Verification: {reason}")
     target.write_text(current,encoding="utf-8")
     result["videos"].append({"index":idx,"id":vid,"title":e.get("title"),"url":f"https://www.youtube.com/watch?v={vid}","published":e.get("upload_date") or e.get("release_date") or e.get("timestamp"),"duration_seconds":e.get("duration"),"transcript":str(target.relative_to(ROOT)),"method":method,"status":status,"coverage_pct":coverage,"verification":reason})
     if status!="COMPLETE_VERIFIED":failed=True
    except Exception as exc:
     failed=True;result["errors"].append({"index":idx,"id":vid,"error":str(exc)[:500]})
    time.sleep(.2)
   result["completed"]=len(result["videos"])==count and not result["errors"] and all(v["status"]=="COMPLETE_VERIFIED" for v in result["videos"])
  except Exception as exc:
   failed=True;result["errors"].append({"stage":"discovery","error":str(exc)[:1000]})
  result["generated_at"]=datetime.datetime.now(datetime.timezone.utc).isoformat()
  done.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
  if not result["completed"]:failed=True
 if failed:sys.exit(1)
if __name__=="__main__":main()
