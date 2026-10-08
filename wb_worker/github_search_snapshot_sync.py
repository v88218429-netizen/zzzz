#!/usr/bin/env python3
"""Generate non-secret WB Analytics search snapshots for GitHub publishing."""
from __future__ import annotations
import datetime as dt, json, os, pathlib, time, urllib.error, urllib.parse, urllib.request

PRICE_URL="https://discounts-prices-api.wildberries.ru/api/v2/list/goods/filter"
SEARCH_URL="https://seller-analytics-api.wildberries.ru/api/v2/search-report/product/search-texts"
SHOPS={"air":("AIR","WB_API_TOKEN_AA"),"hozyushka":("Хозяюшка","WB_API_TOKEN_YV")}

def req(url, token, payload=None, params=None):
    if params:
        url += ("&" if "?" in url else "?")+urllib.parse.urlencode(params)
    data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode()
    base_headers={"Accept":"application/json"}
    if data is not None: base_headers["Content-Type"]="application/json"
    last=None
    for auth in (token, f"Bearer {token}"):
        headers=dict(base_headers)
        headers["Authorization"]=auth
        for n in range(6):
            try:
                r=urllib.request.Request(url,data=data,headers=headers,method="POST" if data is not None else "GET")
                with urllib.request.urlopen(r,timeout=120) as h:
                    raw=h.read().decode()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                last=e
                if e.code in (401,403):
                    detail=e.read().decode("utf-8",errors="replace")[:500]
                    last=RuntimeError(f"HTTP {e.code}: {detail}")
                    break
                if e.code==429 or 500<=e.code<=599:
                    time.sleep(min(45,3*(2**n))); continue
                detail=e.read().decode("utf-8",errors="replace")[:500]
                raise RuntimeError(f"HTTP {e.code}: {detail}") from e
            except (urllib.error.URLError,TimeoutError) as e:
                last=e; time.sleep(min(30,2*(2**n)))
    raise RuntimeError(f"request failed: {last}")

def nm_ids(token):
    obj=req(PRICE_URL,token,params={"limit":1000,"offset":0})
    data=obj.get("data",obj) if isinstance(obj,dict) else {}
    goods=(data.get("listGoods") or data.get("goods") or []) if isinstance(data,dict) else []
    out=[]
    for x in goods:
        try: out.append(int(x.get("nmID") or x.get("nmId")))
        except Exception: pass
    return sorted(set(x for x in out if x>0))

def periods():
    end=dt.datetime.now(dt.timezone.utc).date()-dt.timedelta(days=1)
    start=end-dt.timedelta(days=6); past_end=start-dt.timedelta(days=1); past_start=past_end-dt.timedelta(days=6)
    return start.isoformat(),end.isoformat(),past_start.isoformat(),past_end.isoformat()

def snapshot(token, ids):
    start,end,past_start,past_end=periods(); rows={}
    for off in range(0,len(ids),50):
        payload={
          "currentPeriod":{"start":start,"end":end},
          "pastPeriod":{"start":past_start,"end":past_end},
          "nmIds":ids[off:off+50],"topOrderBy":"openCard",
          "includeSubstitutedSKUs":True,"includeSearchTexts":True,
          "orderBy":{"field":"avgPosition","mode":"asc"},"limit":30
        }
        obj=req(SEARCH_URL,token,payload=payload)
        items=((obj.get("data") or {}).get("items") or []) if isinstance(obj,dict) else []
        for x in items:
            try:
                nm=int(x.get("nmId") or x.get("nmID"))
                q=str(x.get("text") or "").strip()
                p=float((x.get("avgPosition") or {}).get("current"))
            except Exception: continue
            if not q: continue
            rawf=(x.get("frequency") or {}).get("current")
            try: f=float(rawf) if rawf not in (None,"") else None
            except Exception: f=None
            key=f"{nm}|{q.casefold()}"
            rows[key]={"source":"wb_search_report","nm_id":nm,"name":str(x.get("name") or ""),"query":q,"position":p,"frequency":f}
        if off+50<len(ids): time.sleep(2)
    if not rows: raise RuntimeError("zero factual search rows")
    return {"created_at":dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),"period_end":end,"trust_status":"FACTUAL_WB_ANALYTICS","data":rows}

def main():
    out=pathlib.Path(os.environ.get("SEARCH_GITHUB_OUT","wb_data/search")); out.mkdir(parents=True,exist_ok=True)
    manifest={}
    for cabinet,(name,env) in SHOPS.items():
        token=os.environ.get(env,"").strip()
        if not token: raise RuntimeError(f"{env} missing")
        ids=nm_ids(token)
        if not ids: raise RuntimeError(f"{name}: no nmIds")
        print(f"{name}: discovered {len(ids)} nmIds")
        snap=snapshot(token,ids)
        (out/f"{cabinet}.json").write_text(json.dumps(snap,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        manifest[cabinet]={"name":name,"nm_ids":len(ids),"query_rows":len(snap["data"]),"period_end":snap["period_end"],"created_at":snap["created_at"]}
        print(f"{name}: nmIds={len(ids)} query_rows={len(snap['data'])}")
    (out/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("WB_SEARCH_SNAPSHOT=ok")
if __name__=="__main__": main()
