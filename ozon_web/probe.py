#!/usr/bin/env python3
import json, re, sys, time, uuid
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

def headers(referer=None):
    h={
      "accept":"application/json",
      "accept-language":"ru-RU,ru;q=0.9",
      "content-type":"application/json",
      "user-agent":UA,
      "x-o3-app-name":"mweb_client",
      "x-o3-app-version":APP_VERSION,
      "sec-ch-ua":'"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
      "sec-ch-ua-mobile":"?1",
      "sec-ch-ua-platform":'"Android"',
      "x-o3-parent-requestid":uuid.uuid4().hex,
      "x-page-view-id":str(uuid.uuid4()),
    }
    if referer: h["referer"]=referer
    return h

def api_get(session,page_path,referer=None):
    attempts=[]
    for base in API_URLS:
        url=f"{base}?url={quote(page_path,safe='')}"
        try:
            r=session.get(url,headers=headers(referer),timeout=30)
            attempts.append({"endpoint":base,"http":r.status_code})
            if r.status_code==200:
                return r.status_code,r,base,attempts
        except Exception as e:
            attempts.append({"endpoint":base,"error":repr(e)[:180]})
    return (r.status_code if 'r' in locals() else 0),(r if 'r' in locals() else None),None,attempts

def extract_items(data):
    for k,v in (data.get("widgetStates") or {}).items():
        if not str(k).startswith("tileGrid2"): continue
        try:
            st=json.loads(v) if isinstance(v,str) else v
            items=st.get("items") or []
            if items: return items
        except Exception: pass
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

def scan_one(session,task):
    q=task["query"]; target=str(task["sku"]); max_items=int(task.get("max_items",300))
    path=f"/search/?text={quote(q)}&from_global=true"
    ref=f"{BASE_URL}/search/?text={quote(q)}&from_global=true"
    checked=0; page=0
    while checked<max_items and path and page<30:
        page+=1
        code,r,endpoint,attempts=api_get(session,path,ref)
        if code!=200 or r is None:
            return {"status":"http_error","http":code,"position":None,"checked":checked,"page":page,"endpoint":endpoint,"attempts":attempts}
        try:data=r.json()
        except Exception:return {"status":"bad_json","http":code,"position":None,"checked":checked,"page":page}
        items=extract_items(data)
        if not items:
            return {"status":"empty_or_blocked","http":code,"position":None,"checked":checked,"page":page,"endpoint":endpoint,"attempts":attempts}
        for item in items:
            pid=product_id(item)
            if not pid: continue
            checked+=1
            if pid==target:
                return {"status":"ok","http":code,"position":checked,"checked":checked,"page":page,"endpoint":endpoint,"attempts":attempts}
            if checked>=max_items: break
        path=next_page(data)
        time.sleep(0.7)
    return {"status":"not_found","http":200,"position":None,"checked":checked,"page":page,"endpoint":endpoint if 'endpoint' in locals() else None}

def main():
    cfg=json.loads(Path("ozon_web/config.json").read_text(encoding="utf-8"))
    out={"generated_at":datetime.now(timezone.utc).isoformat(),"source":"ozon_web_json","results":[]}
    with requests.Session(impersonate="chrome124") as s:
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
    print(json.dumps(out,ensure_ascii=False,indent=2))
    bad=[x for x in out["results"] if x["status"] in ("http_error","bad_json","empty_or_blocked")]
    return 3 if bad and len(bad)==len(out["results"]) else 0

if __name__=="__main__":
    sys.exit(main())
