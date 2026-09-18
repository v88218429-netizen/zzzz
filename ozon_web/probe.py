#!/usr/bin/env python3
import csv, json, math, os, re, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from curl_cffi import requests

API_URLS=[
 "https://api.ozon.ru/composer-api.bx/page/json/v2",
 "https://api.ozon.ru/api/entrypoint-api.bx/page/json/v2",
 "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2",
]
BASE_URL="https://www.ozon.ru"
UA=("Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36")
APP_VERSION="release_2-3-2026_659ea623"
CSV_FIELDS=["captured_at","article","sku","query","position","previous","delta",
            "status","checked","page","http","endpoint","source"]

def headers(referer=None, accept_json=True):
    h={
      "accept":"application/json" if accept_json else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "accept-language":"ru-RU,ru;q=0.9",
      "user-agent":UA,
      "sec-ch-ua":'"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
      "sec-ch-ua-mobile":"?1",
      "sec-ch-ua-platform":'"Android"',
    }
    if accept_json:
        h.update({
          "content-type":"application/json",
          "x-o3-app-name":"mweb_client",
          "x-o3-app-version":APP_VERSION,
          "x-o3-parent-requestid":uuid.uuid4().hex,
          "x-page-view-id":str(uuid.uuid4()),
        })
    if referer: h["referer"]=referer
    return h

def api_get(session,page_path,referer=None):
    attempts=[]
    last_r=None
    for base in API_URLS:
        url=f"{base}?url={quote(page_path,safe='')}"
        try:
            r=session.get(url,headers=headers(referer,True),timeout=30)
            last_r=r
            attempts.append({"endpoint":base,"http":r.status_code})
            if r.status_code==200:
                return r.status_code,r,base,attempts
        except Exception as e:
            attempts.append({"endpoint":base,"error":repr(e)[:180]})
    return (last_r.status_code if last_r is not None else 0),last_r,None,attempts

def extract_items(data):
    for k,v in (data.get("widgetStates") or {}).items():
        if not str(k).startswith("tileGrid2"): continue
        try:
            st=json.loads(v) if isinstance(v,str) else v
            items=st.get("items") or []
            if items: return items
        except Exception:
            pass
    return []

def product_id(item):
    link=((item.get("action") or {}).get("link") or "")
    m=re.search(r"/product/(?:[^/?]+-)?(\d+)(?:[/?]|$)",link)
    if m: return m.group(1)
    for s in item.get("mainState") or []:
        ci=(s.get("cellTrackingInfo") or {}).get("product") or {}
        if ci.get("id"): return str(ci["id"])
    return None

def next_page(data):
    if data.get("nextPage"): return data["nextPage"]
    try:
        sh=json.loads(data.get("shared","{}")) if isinstance(data.get("shared"),str) else (data.get("shared") or {})
        cat=sh.get("catalog") or {}
        cur=int(cat.get("currentPage") or 1); total=int(cat.get("totalPages") or 1)
        if cur>=total:return None
        url=(data.get("pageInfo") or {}).get("url") or ""
        if not url:return None
        n=cur+1
        if re.search(r"([?&]page)=\d+",url):
            return re.sub(r"([?&]page)=\d+",rf"\g<1>={n}",url)
        return url+("&" if "?" in url else "?")+f"page={n}"
    except Exception:
        return None

def html_product_ids(text):
    patterns=[
      r'https?://www\.ozon\.ru/product/(?:[^"\'<>?]+-)?(\d+)(?:[/?\"\'])',
      r'/product/(?:[^"\'<>?]+-)?(\d+)(?:[/?\"\'])',
      r'\\/product\\/(?:[^"\\?]+-)?(\d+)(?:\\/|\\u002F|\?|")',
    ]
    out=[]; seen=set()
    for pat in patterns:
        for pid in re.findall(pat,text,re.I):
            pid=str(pid)
            if pid not in seen:
                seen.add(pid); out.append(pid)
    return out

def scan_html(session,task,prior_attempts=None):
    q=task["query"]; target=str(task["sku"]); max_items=int(task.get("max_items",300))
    checked=0
    pages=max(1,min(30,math.ceil(max_items/36)))
    attempts=list(prior_attempts or [])
    for page in range(1,pages+1):
        url=f"{BASE_URL}/search/?text={quote(q)}&from_global=true&page={page}"
        try:
            r=session.get(url,headers=headers(BASE_URL,False),timeout=30)
            attempts.append({"endpoint":"html_search","http":r.status_code,"page":page})
        except Exception as e:
            attempts.append({"endpoint":"html_search","error":repr(e)[:180],"page":page})
            return {"status":"html_error","http":0,"position":None,"checked":checked,"page":page,
                    "endpoint":"html_search","attempts":attempts}
        if r.status_code!=200:
            return {"status":"http_error","http":r.status_code,"position":None,"checked":checked,"page":page,
                    "endpoint":"html_search","attempts":attempts}
        ids=html_product_ids(r.text or "")
        if not ids:
            return {"status":"empty_or_blocked","http":200,"position":None,"checked":checked,"page":page,
                    "endpoint":"html_search","attempts":attempts}
        for pid in ids:
            checked+=1
            if pid==target:
                return {"status":"ok","http":200,"position":checked,"checked":checked,"page":page,
                        "endpoint":"html_search","attempts":attempts}
            if checked>=max_items: break
        if checked>=max_items: break
        time.sleep(0.7)
    return {"status":"not_found","http":200,"position":None,"checked":checked,"page":pages,
            "endpoint":"html_search","attempts":attempts}

def scan_one(session,task):
    q=task["query"]; target=str(task["sku"]); max_items=int(task.get("max_items",300))
    path=f"/search/?text={quote(q)}&from_global=true"
    ref=f"{BASE_URL}/search/?text={quote(q)}&from_global=true"
    checked=0; page=0; all_attempts=[]
    while checked<max_items and path and page<30:
        page+=1
        code,r,endpoint,attempts=api_get(session,path,ref)
        all_attempts.extend(attempts)
        if code!=200 or r is None:
            return scan_html(session,task,all_attempts)
        try:
            data=r.json()
        except Exception:
            return scan_html(session,task,all_attempts+[{"endpoint":endpoint,"error":"bad_json"}])
        items=extract_items(data)
        if not items:
            return scan_html(session,task,all_attempts+[{"endpoint":endpoint,"error":"empty_items"}])
        for item in items:
            pid=product_id(item)
            if not pid: continue
            checked+=1
            if pid==target:
                return {"status":"ok","http":code,"position":checked,"checked":checked,"page":page,
                        "endpoint":endpoint,"attempts":all_attempts}
            if checked>=max_items: break
        path=next_page(data)
        time.sleep(0.7)
    return {"status":"not_found","http":200,"position":None,"checked":checked,"page":page,
            "endpoint":endpoint if 'endpoint' in locals() else None,"attempts":all_attempts}

def load_cookie_secret():
    raw=(os.getenv("OZON_COOKIES_JSON") or "").strip()
    if not raw: return {}
    try:
        obj=json.loads(raw)
        if isinstance(obj,list):
            return {str(x["name"]):str(x["value"]) for x in obj if isinstance(x,dict) and x.get("name") and "value" in x}
        if isinstance(obj,dict):
            return {str(k):str(v) for k,v in obj.items()}
    except Exception:
        pass
    out={}
    for p in raw.split(";"):
        if "=" in p:
            k,v=p.split("=",1); out[k.strip()]=v.strip()
    return out

def previous_positions(history_path):
    prev={}
    p=Path(history_path)
    if not p.exists(): return prev
    try:
        with p.open("r",encoding="utf-8",newline="") as f:
            for row in csv.DictReader(f):
                key=(row.get("article",""),row.get("sku",""),row.get("query",""))
                pos=(row.get("position") or "").strip()
                if pos.isdigit(): prev[key]=int(pos)
    except Exception:
        return {}
    return prev

def csv_row(item,prev_map):
    key=(item.get("article",""),item.get("sku",""),item.get("query",""))
    pos=item.get("position")
    prev=prev_map.get(key)
    delta=(pos-prev) if isinstance(pos,int) and isinstance(prev,int) else ""
    return {
      "captured_at":item.get("captured_at",""),
      "article":item.get("article",""),
      "sku":item.get("sku",""),
      "query":item.get("query",""),
      "position":"" if pos is None else pos,
      "previous":"" if prev is None else prev,
      "delta":delta,
      "status":item.get("status",""),
      "checked":item.get("checked",""),
      "page":item.get("page",""),
      "http":item.get("http",""),
      "endpoint":item.get("endpoint",""),
      "source":"ozon_web_json",
    }

def write_csv_outputs(items):
    outdir=Path("ozon_web/results"); outdir.mkdir(parents=True,exist_ok=True)
    history=outdir/"history.csv"
    prev_map=previous_positions(history)
    rows=[csv_row(x,prev_map) for x in items]
    latest=outdir/"latest.csv"
    with latest.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=CSV_FIELDS,extrasaction="ignore")
        for row in rows: w.writerow(row)
    new_history=not history.exists()
    with history.open("a",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=CSV_FIELDS,extrasaction="ignore")
        if new_history: w.writeheader()
        for row in rows: w.writerow(row)

def main():
    cfg=json.loads(Path("ozon_web/config.json").read_text(encoding="utf-8"))
    cookies=load_cookie_secret()
    out={"generated_at":datetime.now(timezone.utc).isoformat(),"source":"ozon_web_json",
         "cookie_count":len(cookies),"results":[]}
    with requests.Session(impersonate="chrome124") as s:
        if cookies: s.cookies.update(cookies)
        try:
            s.get(BASE_URL+"/",headers=headers(None,False),timeout=20)
        except Exception:
            pass
        for t in cfg.get("tasks",[]):
            res=scan_one(s,t)
            out["results"].append({
              "captured_at":datetime.now(timezone.utc).isoformat(),
              "article":t.get("article",""),"sku":str(t["sku"]),"query":t["query"],
              "max_items":int(t.get("max_items",300)),**res
            })
            time.sleep(1.0)
    Path("ozon_web/results").mkdir(parents=True,exist_ok=True)
    Path("ozon_web/results/latest.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    write_csv_outputs(out["results"])
    print(json.dumps(out,ensure_ascii=False,indent=2))
    bad=[x for x in out["results"] if x["status"] in ("http_error","bad_json","empty_or_blocked","html_error")]
    return 3 if bad and len(bad)==len(out["results"]) else 0

if __name__=="__main__":
    sys.exit(main())
