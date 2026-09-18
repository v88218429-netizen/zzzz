#!/usr/bin/env python3
import json, os, re, sys, time
from pathlib import Path
import requests

CITY_SLUGS = {
  "Москва":"moscow",
  "Ростов-на-Дону":"rostov_on_don",
  "Краснодар":"krasnodar",
  "Санкт-Петербург":"saint_petersburg",
  "Казань":"kazan",
  "Самара":"samara",
  "Екатеринбург":"yekaterinburg",
  "Новосибирск":"novosibirsk",
}

def creds(city):
    provider=(os.getenv("OZON_PROXY_PROVIDER") or "").strip().lower()
    user=(os.getenv("OZON_PROXY_USER") or "").strip()
    password=(os.getenv("OZON_PROXY_PASS") or "").strip()
    host=(os.getenv("OZON_PROXY_HOST") or ("gate.decodo.com" if provider=="decodo" else "")).strip()
    port=int(os.getenv("OZON_PROXY_PORT") or ("7000" if provider=="decodo" else "0"))
    slug=CITY_SLUGS.get(city, city.lower().replace(" ","_"))
    session=re.sub(r"[^a-z0-9]","",f"preflight{slug}{int(time.time())}")[-24:]
    if provider=="decodo":
        prefix=user if user.startswith("user-") else "user-"+user
        u=f"{prefix}-country-ru-city-{slug}-session-{session}"
        check="https://ip.decodo.com/json"
    elif provider=="brightdata":
        u=f"{user}-country-ru-city-{slug}-session-{session}"
        check="https://geo.brdtest.com/welcome.txt"
    else:
        raise RuntimeError("Unsupported OZON_PROXY_PROVIDER")
    return provider,u,password,host,port,check

def main():
    cfg=json.loads(Path("ozon_live/config.json").read_text(encoding="utf-8"))
    cities=[]
    for task in cfg.get("tasks",[]):
        for c in task.get("cities",[]):
            if c not in cities: cities.append(c)
    out={"ok":True,"cities":[]}
    for city in cities:
        try:
            provider,u,pwd,host,port,url=creds(city)
            proxy=f"http://{u}:{pwd}@{host}:{port}"
            r=requests.get(url,proxies={"http":proxy,"https":proxy},timeout=30)
            ok=200<=r.status_code<300
            item={"city":city,"provider":provider,"ok":ok,"http":r.status_code,"response":r.text[:1200]}
            if not ok: out["ok"]=False
        except Exception as e:
            item={"city":city,"ok":False,"error":repr(e)[:500]}
            out["ok"]=False
        out["cities"].append(item)
    Path("ozon_live/results").mkdir(parents=True,exist_ok=True)
    Path("ozon_live/results/proxy_health.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(out,ensure_ascii=False,indent=2))
    if not out["ok"]: sys.exit(3)

if __name__=="__main__":
    main()
