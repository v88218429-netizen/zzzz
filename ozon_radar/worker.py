#!/usr/bin/env python3
import argparse
import fcntl
import hashlib
import html
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

SHEET_ID=os.getenv("OZON_SHEET_ID","1SHY1rz7XZeqOGkJSkitfSPlKs4cshS_5U63NSv0TO4c")
HOME=Path.home()/".ozon-radar"
DB_PATH=HOME/"radar.sqlite3"
LOCK_PATH=HOME/"worker.lock"
PROFILE_DIR=HOME/"chrome-profile"
LOG_DIR=HOME/"logs"
CLASP=Path.home()/".clasprc.json"
RADAR_SHEET="06_Радар_1мин"
HISTORY_SHEET="06_Радар_История"
QUEUE_SHEET="06_Радар_Очередь"
SOURCE="LIVE WEB · Chrome CDP"
HEARTBEAT_TO_SHEET_SEC=60
CDP_PORT=9227
CDP_URL=f"http://127.0.0.1:{CDP_PORT}"
CHROME_PATHS=[
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
]

class BlockedError(RuntimeError):
    pass

def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def now_display():
    return datetime.now().astimezone().strftime("%d.%m.%Y %H:%M:%S")

def as_int(value, default=0):
    try:
        return int(float(str(value).replace(",",".").strip()))
    except Exception:
        return default

def as_bool(value):
    return value is True or str(value).strip().upper() in ("TRUE","1","ДА","YES","ON")

def walk_find(obj,names):
    if isinstance(obj,dict):
        for k,v in obj.items():
            if k in names and isinstance(v,(str,int,float)):
                return str(v)
        for v in obj.values():
            r=walk_find(v,names)
            if r:
                return r
    elif isinstance(obj,list):
        for v in obj:
            r=walk_find(v,names)
            if r:
                return r
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

def google_request(token,method,url,payload=None):
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
        raise RuntimeError(f"Google API HTTP {e.code}: {body[:700]}")

def get_token():
    creds=load_creds()
    token=creds["access_token"] or refresh_access(creds)
    if not token:
        raise RuntimeError("Google OAuth token unavailable")
    try:
        values_get(token,f"{RADAR_SHEET}!A1:A1")
        return token
    except RuntimeError as e:
        if "HTTP 401" not in str(e):
            raise
    token=refresh_access(creds)
    if not token:
        raise RuntimeError("Google OAuth refresh failed")
    values_get(token,f"{RADAR_SHEET}!A1:A1")
    return token

def values_get(token,a1):
    q=urllib.parse.quote(a1,safe="")
    return google_request(token,"GET",f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{q}?majorDimension=ROWS").get("values",[])

def values_append(token,a1,values):
    q=urllib.parse.quote(a1,safe="")
    return google_request(
        token,"POST",
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{q}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS",
        {"range":a1,"majorDimension":"ROWS","values":values},
    )

def values_batch_update(token,data):
    return google_request(
        token,"POST",
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values:batchUpdate",
        {"valueInputOption":"RAW","data":data},
    )

def init_db():
    HOME.mkdir(parents=True,exist_ok=True)
    LOG_DIR.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(DB_PATH)
    con.execute("""
      CREATE TABLE IF NOT EXISTS state(
        key TEXT PRIMARY KEY,
        article TEXT,
        sku TEXT,
        query TEXT,
        last_position INTEGER,
        last_checked REAL,
        last_sheet_at REAL,
        last_alert_at REAL,
        alert_active INTEGER DEFAULT 0,
        last_error TEXT
      )
    """)
    con.execute("""
      CREATE TABLE IF NOT EXISTS checks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        key TEXT NOT NULL,
        article TEXT,
        sku TEXT,
        query TEXT,
        position INTEGER,
        status TEXT,
        response_ms INTEGER,
        source TEXT
      )
    """)
    con.commit()
    return con

def key_for(article,sku,query):
    return hashlib.sha256(f"{article}|{sku}|{query}".encode("utf-8")).hexdigest()

def db_state(con,key):
    row=con.execute(
        "SELECT last_position,last_checked,last_sheet_at,last_alert_at,alert_active,last_error FROM state WHERE key=?",
        (key,),
    ).fetchone()
    if not row:
        return {
            "last_position":None,"last_checked":0.0,"last_sheet_at":0.0,
            "last_alert_at":0.0,"alert_active":0,"last_error":""
        }
    return {
        "last_position":row[0],"last_checked":row[1] or 0.0,"last_sheet_at":row[2] or 0.0,
        "last_alert_at":row[3] or 0.0,"alert_active":row[4] or 0,"last_error":row[5] or ""
    }

def db_save_state(con,key,task,position,checked_at,last_sheet_at,last_alert_at,alert_active,last_error):
    con.execute("""
      INSERT INTO state(key,article,sku,query,last_position,last_checked,last_sheet_at,last_alert_at,alert_active,last_error)
      VALUES(?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(key) DO UPDATE SET
        article=excluded.article,sku=excluded.sku,query=excluded.query,
        last_position=excluded.last_position,last_checked=excluded.last_checked,
        last_sheet_at=excluded.last_sheet_at,last_alert_at=excluded.last_alert_at,
        alert_active=excluded.alert_active,last_error=excluded.last_error
    """,(
        key,task["article"],task["sku"],task["query"],position,checked_at,last_sheet_at,
        last_alert_at,alert_active,last_error,
    ))
    con.commit()

def db_add_check(con,key,task,position,status,response_ms):
    con.execute("""
      INSERT INTO checks(ts,key,article,sku,query,position,status,response_ms,source)
      VALUES(?,?,?,?,?,?,?,?,?)
    """,(now_iso(),key,task["article"],task["sku"],task["query"],position,status,response_ms,SOURCE))
    con.commit()

def chrome_path():
    for p in CHROME_PATHS:
        if Path(p).exists():
            return p
    raise RuntimeError("Google Chrome not found in /Applications")

def extract_ids(hrefs):
    out=[]
    seen=set()
    for href in hrefs:
        m=re.search(r"/product/(?:[^/?#]+-)?(\d+)(?:[/?#]|$)",str(href))
        if not m:
            continue
        pid=m.group(1)
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out

class LiveScanner:
    def __init__(self):
        PROFILE_DIR.mkdir(parents=True,exist_ok=True)
        LOG_DIR.mkdir(parents=True,exist_ok=True)
        self._ensure_real_chrome()
        self.pw=sync_playwright().start()
        self.browser=self.pw.chromium.connect_over_cdp(CDP_URL)
        contexts=self.browser.contexts
        if not contexts:
            raise RuntimeError("Chrome CDP connected but no browser context exists")
        self.ctx=contexts[0]
        self.page=self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self._warmup_if_needed()

    def _cdp_ready(self):
        try:
            with urllib.request.urlopen(CDP_URL+"/json/version",timeout=1.5) as r:
                return r.status==200
        except Exception:
            return False

    def _ensure_real_chrome(self):
        if self._cdp_ready():
            return

        chrome=chrome_path()
        chrome_log=(LOG_DIR/"chrome.log").open("ab")
        args=[
            chrome,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            "--window-size=1280,900",
            "--window-position=40,40",
            "https://www.ozon.ru/",
        ]
        subprocess.Popen(
            args,
            stdout=chrome_log,
            stderr=chrome_log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

        deadline=time.time()+25
        while time.time()<deadline:
            if self._cdp_ready():
                return
            time.sleep(0.5)
        raise RuntimeError("Real Chrome started but CDP port 9227 did not become ready")

    def _save_diag(self,label):
        stamp=datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            self.page.screenshot(path=str(LOG_DIR/f"{label}-{stamp}.png"),full_page=False)
        except Exception:
            pass
        try:
            (LOG_DIR/f"{label}-{stamp}.html").write_text(
                self.page.content()[:500000],
                encoding="utf-8",
                errors="ignore",
            )
        except Exception:
            pass

    def _warmup_if_needed(self,force=False):
        marker=HOME/"ozon-warmup-ok"
        if marker.exists() and not force:
            return
        try:
            response=self.page.goto("https://www.ozon.ru/",wait_until="domcontentloaded",timeout=45000)
            status=response.status if response else 0
            self.page.wait_for_timeout(3500)
            if status in (401,403,429):
                self._save_diag("warmup-blocked")
                if force:
                    raise BlockedError(f"Ozon home HTTP {status}")
                return
            marker.write_text(now_iso(),encoding="utf-8")
        except PlaywrightTimeoutError:
            if force:
                raise BlockedError("Ozon home timeout")

    def close(self):
        # Chrome is intentionally kept alive between 1-minute worker runs.
        try:
            self.pw.stop()
        except Exception:
            pass

    def _goto_search(self,url):
        try:
            response=self.page.goto(url,wait_until="domcontentloaded",timeout=45000)
        except PlaywrightTimeoutError:
            self._save_diag("search-timeout")
            raise BlockedError("Ozon search timeout")

        status=response.status if response else 0
        if status in (401,403,429):
            # Retry once after opening the real home page. This gives Ozon a
            # normal browser warm-up and keeps cookies in the persistent profile.
            self._warmup_if_needed(force=True)
            self.page.wait_for_timeout(1800)
            try:
                response=self.page.goto(url,wait_until="domcontentloaded",timeout=45000)
            except PlaywrightTimeoutError:
                self._save_diag("search-retry-timeout")
                raise BlockedError("Ozon search retry timeout")
            status=response.status if response else 0

        if status in (401,403,429):
            self._save_diag(f"search-http-{status}")
            raise BlockedError(f"Ozon real Chrome HTTP {status}")

        return status

    def scan(self,query,sku,max_position):
        target=str(sku)
        checked=0
        pages=max(1,min(5,math.ceil(max_position/36)))
        started=time.time()
        last_http=0

        for page_no in range(1,pages+1):
            params={"text":query,"from_global":"true"}
            if page_no>1:
                params["page"]=str(page_no)
            url="https://www.ozon.ru/search/?"+urllib.parse.urlencode(params)
            last_http=self._goto_search(url)

            self.page.wait_for_timeout(2200)
            try:
                self.page.evaluate("window.scrollTo(0, Math.min(document.body.scrollHeight, 3000))")
                self.page.wait_for_timeout(800)
            except Exception:
                pass

            hrefs=self.page.locator("main a[href*='/product/']").evaluate_all("(els)=>els.map(e=>e.href)")
            if not hrefs:
                hrefs=self.page.locator("a[href*='/product/']").evaluate_all("(els)=>els.map(e=>e.href)")
            ids=extract_ids(hrefs)

            if len(ids)<3:
                body=(self.page.locator("body").inner_text(timeout=3000) or "")[:5000].lower()
                self._save_diag("too-few-cards")
                if "доступ ограничен" in body or "access denied" in body or "captcha" in body:
                    raise BlockedError("Ozon anti-bot page in real Chrome")
                raise BlockedError(f"Ozon returned too few product cards ({len(ids)})")

            for pid in ids:
                checked+=1
                if pid==target:
                    return {
                        "status":"ok","position":checked,"http":last_http,
                        "response_ms":int((time.time()-started)*1000),
                    }
                if checked>=max_position:
                    break
            if checked>=max_position:
                break

        return {
            "status":"not_found","position":None,"http":last_http,
            "response_ms":int((time.time()-started)*1000),
        }

def load_tasks(token):
    rows=values_get(token,f"{RADAR_SHEET}!A5:P1000")
    out=[]
    for i,row in enumerate(rows,start=5):
        row=list(row)+[""]*(16-len(row))
        if not as_bool(row[0]):
            continue
        article=str(row[1]).strip()
        sku=str(row[2]).strip()
        query=str(row[3]).strip()
        if not (article and sku and query):
            continue
        out.append({
            "row":i,"article":article,"sku":sku,"query":query,
            "interval_min":max(1,as_int(row[4],1)),
            "drop_threshold":max(1,as_int(row[5],3)),
            "top_boundary":max(1,as_int(row[6],10)),
            "max_position":max(10,min(200,as_int(row[7],100))),
            "sheet_position":as_int(row[8],0) or None,
        })
    return out

def event_message(task,prev,current,event_type):
    cur_txt=f">{task['max_position']}" if current is None else str(current)
    prev_txt=f">{task['max_position']}" if prev is None else str(prev)
    if current is None:
        drop_txt=f"вышел за TOP-{task['max_position']}"
    else:
        drop=current-prev if prev is not None else 0
        drop_txt=f"падение на {drop}" if drop>0 else f"изменение {drop}"

    if event_type=="RECOVERY":
        title="✅ <b>Ozon · позиция восстановилась</b>"
    else:
        title="🔴 <b>Ozon · падение позиции</b>"

    return (
        title+"\n\n"+
        "Артикул: <b>"+html.escape(task["article"])+"</b>\n"+
        "Запрос: <b>"+html.escape(task["query"])+"</b>\n"+
        "Позиция: <b>"+html.escape(prev_txt)+" → "+html.escape(cur_txt)+"</b>\n"+
        "Событие: "+html.escape(drop_txt)+"\n"+
        "Контроль: TOP-"+str(task["top_boundary"])+" · порог "+str(task["drop_threshold"])+" поз.\n"+
        "Проверено: "+html.escape(now_display())
    )

def event_id(task,prev,current,event_type):
    raw=f"{task['article']}|{task['sku']}|{task['query']}|{prev}|{current}|{event_type}|{int(time.time()//60)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]

def run_once():
    HOME.mkdir(parents=True,exist_ok=True)
    LOG_DIR.mkdir(parents=True,exist_ok=True)

    with open(LOCK_PATH,"w") as lockf:
        try:
            fcntl.flock(lockf,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another Ozon radar run is active; skip.")
            return 0

        token=get_token()
        tasks=load_tasks(token)
        con=init_db()
        due=[]
        now=time.time()
        for task in tasks:
            st=db_state(con,key_for(task["article"],task["sku"],task["query"]))
            if now-(st["last_checked"] or 0)>=task["interval_min"]*60-5:
                due.append((task,st))

        if not due:
            print(json.dumps({"status":"idle","active":len(tasks)},ensure_ascii=False))
            return 0

        updates=[]
        history_rows=[]
        queue_rows=[]
        scanner=None
        ok_count=0
        err_count=0

        try:
            scanner=LiveScanner()
            for task,st in due:
                key=key_for(task["article"],task["sku"],task["query"])
                checked_at=time.time()
                prev=st["last_position"]
                if prev is None and task.get("sheet_position") is not None:
                    prev=task["sheet_position"]
                last_sheet_at=st["last_sheet_at"]
                last_alert_at=st["last_alert_at"]
                alert_active=int(st["alert_active"] or 0)
                last_error=""

                try:
                    res=scanner.scan(task["query"],task["sku"],task["max_position"])
                    pos=res["position"]
                    status=res["status"]
                    response_ms=res["response_ms"]
                    db_add_check(con,key,task,pos,status,response_ms)

                    cur_eff=pos if pos is not None else task["max_position"]+1
                    prev_eff=prev if prev is not None else None
                    delta="" if prev_eff is None else prev_eff-cur_eff
                    event_type=""
                    message=""

                    if prev_eff is not None:
                        drop=cur_eff-prev_eff
                        crossed=prev_eff<=task["top_boundary"] and cur_eff>task["top_boundary"]
                        material=drop>=task["drop_threshold"]
                        if crossed or material:
                            event_type="OUT_TOP" if crossed else "DROP"
                            message=event_message(task,prev_eff,pos,event_type)
                            alert_active=1
                            last_alert_at=checked_at
                        elif alert_active and cur_eff<=task["top_boundary"]:
                            event_type="RECOVERY"
                            message=event_message(task,prev_eff,pos,event_type)
                            alert_active=0
                            last_alert_at=checked_at

                    if event_type:
                        queue_rows.append([
                            event_id(task,prev_eff,pos,event_type),now_iso(),task["article"],task["sku"],
                            task["query"],"" if prev_eff is None else prev_eff,
                            f">{task['max_position']}" if pos is None else pos,
                            "" if prev_eff is None else prev_eff-cur_eff,
                            event_type,message,"PENDING","",0,""
                        ])

                    changed=(prev_eff is None) or (cur_eff!=prev_eff)
                    if changed or checked_at-last_sheet_at>=HEARTBEAT_TO_SHEET_SEC:
                        history_rows.append([
                            now_iso(),task["article"],task["sku"],task["query"],
                            f">{task['max_position']}" if pos is None else pos,
                            "" if prev_eff is None else prev_eff,
                            "" if prev_eff is None else prev_eff-cur_eff,
                            status,SOURCE,res["http"],response_ms,
                            hashlib.sha1(f"{checked_at}|{key}".encode()).hexdigest()[:12],
                        ])
                        last_sheet_at=checked_at

                    status_text="OK" if pos is not None else f"НЕ НАЙДЕН ≤{task['max_position']}"
                    alert_text="" if not last_alert_at else datetime.fromtimestamp(last_alert_at).astimezone().strftime("%d.%m.%Y %H:%M")
                    updates.append({
                        "range":f"{RADAR_SHEET}!I{task['row']}:O{task['row']}",
                        "majorDimension":"ROWS",
                        "values":[[
                            f">{task['max_position']}" if pos is None else pos,
                            "" if prev_eff is None else prev_eff,
                            "" if prev_eff is None else prev_eff-cur_eff,
                            now_display(),status_text,alert_text,SOURCE
                        ]]
                    })
                    db_save_state(con,key,task,cur_eff,checked_at,last_sheet_at,last_alert_at,alert_active,"")
                    ok_count+=1

                except Exception as e:
                    last_error=str(e)[:240]
                    status_text="ОШИБКА · "+last_error
                    updates.append({
                        "range":f"{RADAR_SHEET}!L{task['row']}:O{task['row']}",
                        "majorDimension":"ROWS",
                        "values":[[now_display(),status_text,"",SOURCE]]
                    })
                    db_add_check(con,key,task,prev,"error",0)
                    db_save_state(
                        con,key,task,prev,checked_at,last_sheet_at,last_alert_at,alert_active,last_error
                    )
                    err_count+=1
        finally:
            if scanner is not None:
                scanner.close()
            con.close()

        if updates:
            values_batch_update(token,updates)
        if history_rows:
            values_append(token,f"{HISTORY_SHEET}!A:L",history_rows)
        if queue_rows:
            values_append(token,f"{QUEUE_SHEET}!A:N",queue_rows)

        health={
            "checked_at":now_iso(),"active":len(tasks),"due":len(due),
            "ok":ok_count,"errors":err_count,"alerts":len(queue_rows),
        }
        (HOME/"health.json").write_text(json.dumps(health,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(health,ensure_ascii=False))
        return 0 if ok_count>0 else 4

def probe(query,sku,max_position):
    scanner=None
    try:
        scanner=LiveScanner()
        result=scanner.scan(query,sku,max_position)
        print(json.dumps({"query":query,"sku":sku,**result},ensure_ascii=False))
        return 0
    except Exception as e:
        print(json.dumps({"query":query,"sku":sku,"status":"error","error":str(e)},ensure_ascii=False))
        return 3
    finally:
        if scanner is not None:
            scanner.close()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--once",action="store_true")
    ap.add_argument("--probe",nargs=2,metavar=("QUERY","SKU"))
    ap.add_argument("--max-position",type=int,default=30)
    args=ap.parse_args()
    if args.probe:
        return probe(args.probe[0],args.probe[1],args.max_position)
    return run_once()

if __name__=="__main__":
    sys.exit(main())
