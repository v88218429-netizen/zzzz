import os, re, json, uuid, time
from urllib.parse import quote
from curl_cffi import requests

API_URLS=["https://api.ozon.ru/composer-api.bx/page/json/v2","https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2"]
BASE_URL="https://www.ozon.ru"
QUERY=os.getenv("OZON_SERP_QUERY","5094364543")
TARGET=str(os.getenv("OZON_SERP_SKU","5094364543"))
MAX_PAGES=int(os.getenv("OZON_SERP_MAX_PAGES","20"))

UA=("Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Mobile Safari/537.36")

def headers(ref=None):
    h={
        "accept":"application/json",
        "accept-language":"ru-RU,ru;q=0.9",
        "content-type":"application/json",
        "user-agent":UA,
        "x-o3-app-name":"mweb_client",
        "x-o3-app-version":"release_2-3-2026_659ea623",
        "sec-ch-ua":'"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
        "sec-ch-ua-mobile":"?1",
        "sec-ch-ua-platform":'"Android"',
        "x-o3-parent-requestid":uuid.uuid4().hex,
        "x-page-view-id":str(uuid.uuid4()),
    }
    if ref: h["referer"]=ref
    return h

def api_get(session,path,ref=None):
    last=None
    for base in API_URLS:
        url=base+"?url="+quote(path,safe="")
        r=session.get(url,headers=headers(ref),timeout=30)
        print("ENDPOINT",base,"HTTP",r.status_code,"PATH",path[:180])
        if r.status_code==200:
            return r.json()
        print("BODY",r.text[:500])
        last=r
    last.raise_for_status()

def extract_items(data):
    for key,val in (data.get("widgetStates") or {}).items():
        if not key.startswith("tileGrid2"):
            continue
        try:
            state=json.loads(val) if isinstance(val,str) else val
            items=state.get("items") or []
            if items: return items
        except Exception:
            pass
    return []

def sku_from_item(item):
    link=((item.get("action") or {}).get("link") or "")
    m=re.search(r"-(\d{6,})(?:/|\?|$)",link)
    if m: return m.group(1),link
    raw=json.dumps(item,ensure_ascii=False)
    if TARGET in raw: return TARGET,link
    return None,link

def next_path(data,current):
    if data.get("nextPage"): return data["nextPage"]
    try:
        shared=data.get("shared","{}")
        if isinstance(shared,str): shared=json.loads(shared)
        cat=(shared or {}).get("catalog") or {}
        cur=int(cat.get("currentPage") or 1)
        total=int(cat.get("totalPages") or 1)
        if cur>=total: return None
        p=(data.get("pageInfo") or {}).get("url") or current
        nxt=cur+1
        if re.search(r"([?&]page)=\d+",p):
            p=re.sub(r"([?&]page)=\d+",rf"\g<1>={nxt}",p)
        else:
            p += ("&" if "?" in p else "?")+f"page={nxt}"
        if re.search(r"([?&]layout_page_index)=\d+",p):
            p=re.sub(r"([?&]layout_page_index)=\d+",rf"\g<1>={nxt}",p)
        return p
    except Exception as e:
        print("NEXT_ERR",repr(e))
        return None

def main():
    path=f"/search/?text={QUERY}&from_global=true"
    ref=f"{BASE_URL}/search/?text={quote(QUERY)}&from_global=true"
    absolute_pos=0
    with requests.Session(impersonate="chrome124") as s:
        for page in range(1,MAX_PAGES+1):
            data=api_get(s,path,ref)
            items=extract_items(data)
            print("PAGE",page,"ITEMS",len(items))
            if not items: return 4
            for item in items:
                absolute_pos+=1
                sku,link=sku_from_item(item)
                if sku==TARGET:
                    out={
                        "query":QUERY,
                        "sku":TARGET,
                        "position":absolute_pos,
                        "page":page,
                        "pagePosition":1+((absolute_pos-1)%len(items)),
                        "link":link,
                        "ts":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
                    }
                    print("SERP_RESULT",json.dumps(out,ensure_ascii=False))
                    return 0
            nxt=next_path(data,path)
            if not nxt: break
            path=nxt
            ref=BASE_URL+path
            time.sleep(0.6)
    print("SERP_NOT_FOUND",json.dumps({"query":QUERY,"sku":TARGET,"checked":absolute_pos},ensure_ascii=False))
    return 3

if __name__=="__main__":
    raise SystemExit(main())
