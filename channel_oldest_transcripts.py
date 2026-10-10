#!/usr/bin/env python3
"""Transcribe the 15 oldest public long-form uploads of a YouTube channel."""
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
def main():
 failed=False
 for req in sorted(REQ.glob("*.json")):
  config=json.loads(req.read_text())
  task=config["task_id"]; channel=config["channel_url"].rstrip("/"); count=int(config.get("count",15))
  done=REPORT/(req.stem+".json")
  if done.exists() and json.loads(done.read_text()).get("completed"):continue
  result={"task_id":task,"channel":channel,"requested":count,"videos":[],"errors":[],"completed":False}
  try:
   raw=cmd(["yt-dlp","--flat-playlist","--dump-single-json","--no-warnings",channel+"/videos"])
   entries=[e for e in json.loads(raw).get("entries",[]) if e and e.get("id") and e.get("id")!= "NA"]
   # Channel /videos playlist is newest-first, so its last items are oldest.
   if len(entries)<count:raise RuntimeError(f"Only {len(entries)} video entries retrieved; expected at least {count}")
   selected=list(reversed(entries[-count:]))
   for idx,e in enumerate(selected,1):
    vid=e["id"]; target=OUT/f"{task}_{idx:02d}_{vid}.md"
    try:
     if target.exists() and "## Transcript" in target.read_text():method="existing"
     else:
      body,method=transcript(vid)
      target.write_text(f"# {e.get('title',vid)}\n\n- Source: https://www.youtube.com/watch?v={vid}\n- Channel: {channel}\n- Oldest-first index: {idx}\n- Transcript method: {method}\n\n## Transcript\n\n{body}\n",encoding="utf-8")
     result["videos"].append({"index":idx,"id":vid,"title":e.get("title"),"url":f"https://www.youtube.com/watch?v={vid}","transcript":str(target.relative_to(ROOT)),"method":method})
    except Exception as exc:
     failed=True;result["errors"].append({"index":idx,"id":vid,"error":str(exc)[:500]})
    time.sleep(.2)
   result["completed"]=len(result["videos"])==count and not result["errors"]
  except Exception as exc:
   failed=True;result["errors"].append({"stage":"discovery","error":str(exc)[:1000]})
  result["generated_at"]=datetime.datetime.now(datetime.timezone.utc).isoformat()
  done.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
  if not result["completed"]:failed=True
 if failed:sys.exit(1)
if __name__=="__main__":main()
