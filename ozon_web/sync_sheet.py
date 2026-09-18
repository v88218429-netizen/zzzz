#!/usr/bin/env python3
import csv, json, os, sys, urllib.parse, urllib.request, urllib.error
from pathlib import Path

SPREADSHEET_ID=os.getenv("OZON_SHEET_ID","1SHY1rz7XZeqOGkJSkitfSPlKs4cshS_5U63NSv0TO4c")
CLASP=Path.home()/".clasprc.json"
LATEST_JSON=Path("ozon_web/results/latest.json")
HISTORY_CSV=Path("ozon_web/results/history.csv")

def walk_find(obj,names):
    if isinstance(obj,dict):
        for k,v in obj.items():
            if k in names and isinstance(v,(str,int,float)):
                return str(v)
        for v in obj.values():
            r=walk_find(v,names)
            if r: return r
    elif isinstance(obj,list):
        for v in obj:
            r=walk_find(v,names)
            if r: return r
    return ""

def load_creds():
    if not CLASP.exists():
        raise RuntimeError("~/.clasprc.json not found")
    obj=json.loads(CLASP.read_text(encoding="utf-8"))
    return {
      "access_token":walk_find(obj,{"access_token","accessToken"}),
      "refresh_token":walk_find(obj,{"refresh_token","refreshToken"}),
      "client_id":walk_find(obj,{"client_id","clientId"}),
      "client_secret":walk_find(obj,{"client_secret","clientSecret"}),
    }

def refresh_access(creds):
    if not (creds["refresh_token"] and creds["client_id"] and creds["client_secret"]):
        return creds["access_token"]
    body=urllib.parse.urlencode({
      "client_id":creds["client_id"],
      "client_secret":creds["client_secret"],
      "refresh_token":creds["refresh_token"],
      "grant_type":"refresh_token",
    }).encode()
    req=urllib.request.Request("https://oauth2.googleapis.com/token",data=body,method="POST")
    req.add_header("Content-Type","application/x-www-form-urlencoded")
    with urllib.request.urlopen(req,timeout=30) as r:
        data=json.loads(r.read().decode())
    return data.get("access_token") or creds["access_token"]

def request(token,method,url,payload=None):
    data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode("utf-8")
    req=urllib.request.Request(url,data=data,method=method)
    req.add_header("Authorization","Bearer "+token)
    if payload is not None:
        req.add_header("Content-Type","application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req,timeout=45) as r:
            raw=r.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body=e.read().decode("utf-8","replace")
        raise RuntimeError(f"Google API HTTP {e.code}: {body[:500]}")

def values_get(token,a1):
    q=urllib.parse.quote(a1,safe="")
    return request(token,"GET",f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{q}?majorDimension=ROWS").get("values",[])

def values_update(token,a1,values):
    q=urllib.parse.quote(a1,safe="")
    return request(token,"PUT",f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{q}?valueInputOption=RAW",{"range":a1,"majorDimension":"ROWS","values":values})

def values_clear(token,a1):
    q=urllib.parse.quote(a1,safe="")
    return request(token,"POST",f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{q}:clear",{})

def values_append(token,a1,values):
    q=urllib.parse.quote(a1,safe="")
    return request(token,"POST",f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{q}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS",{"range":a1,"majorDimension":"ROWS","values":values})

def latest_rows():
    data=json.loads(LATEST_JSON.read_text(encoding="utf-8"))
    results=data.get("results") or []
    hist=[]
    if HISTORY_CSV.exists():
        with HISTORY_CSV.open("r",encoding="utf-8",newline="") as f:
            hist=list(csv.DictReader(f))
    by_key={}
    for row in hist:
        key=(row.get("captured_at",""),row.get("article",""),row.get("sku",""),row.get("query",""))
        by_key[key]=row
    out=[]
    for item in results:
        key=(str(item.get("captured_at","")),str(item.get("article","")),str(item.get("sku","")),str(item.get("query","")))
        h=by_key.get(key,{})
        out.append([
          item.get("captured_at",""),
          item.get("article",""),
          str(item.get("sku","")),
          item.get("query",""),
          "" if item.get("position") is None else item.get("position"),
          h.get("previous",""),
          h.get("delta",""),
          item.get("status",""),
          item.get("checked",""),
          item.get("page",""),
          item.get("http",""),
          item.get("endpoint",""),
          data.get("source","ozon_web_json"),
        ])
    return data,out

def main():
    data,rows=latest_rows()
    if not rows:
        print("No Ozon rows to sync")
        return 0
    creds=load_creds()
    token=creds["access_token"] or refresh_access(creds)
    if not token:
        raise RuntimeError("No Google OAuth access token")
    try:
        values_get(token,"00_Пульт!A1:A2")
    except RuntimeError as e:
        if "HTTP 401" not in str(e):
            raise
        token=refresh_access(creds)
        if not token: raise
        values_get(token,"00_Пульт!A1:A2")

    # current snapshot
    values_clear(token,"04_Web_Позиции!A3:M5000")
    values_update(token,f"04_Web_Позиции!A3:M{2+len(rows)}",rows)

    # append only unseen captured rows into history
    existing=values_get(token,"04_Web_История!A2:D20000")
    seen={(r[0] if len(r)>0 else "",r[1] if len(r)>1 else "",r[2] if len(r)>2 else "",r[3] if len(r)>3 else "") for r in existing}
    fresh=[r for r in rows if (str(r[0]),str(r[1]),str(r[2]),str(r[3])) not in seen]
    if fresh:
        values_append(token,"04_Web_История!A:M",fresh)

    ok=sum(1 for r in rows if str(r[7]).lower() in ("ok","not_found"))
    errors=len(rows)-ok
    last=max(str(r[0]) for r in rows)
    if errors:
        status=f"ОШИБКА · {errors}/{len(rows)}"
    else:
        found=sum(1 for r in rows if str(r[4]).strip()!="")
        status=f"OK · позиций {found}/{len(rows)}"
    values_update(token,"00_Пульт!C5:E5",[[last,"статус",status]])
    values_append(token,"99_Лог!A:D",[[last,"OZON_WEB_SYNC","DONE" if not errors else "ERROR",status]])
    print(json.dumps({"synced":len(rows),"fresh_history":len(fresh),"status":status},ensure_ascii=False))
    return 0

if __name__=="__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("SYNC_ERROR:",repr(e))
        sys.exit(4)
