#!/usr/bin/env python3
import argparse, json, os, random, re, sys, time, subprocess, tempfile, zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

SKU_RE = re.compile(r"/product/(?:[^/?]+-)?(\d+)(?:[/?]|$)")
SEARCH_URL = "https://www.ozon.ru/search/?text={q}&page={page}"
CITY_COORDS = {
    "Москва": (55.7558, 37.6173),
    "Ростов-на-Дону": (47.2357, 39.7015),
    "Краснодар": (45.0355, 38.9753),
    "Санкт-Петербург": (59.9343, 30.3351),
    "Казань": (55.7961, 49.1064),
    "Самара": (53.1959, 50.1002),
    "Екатеринбург": (56.8389, 60.6057),
    "Новосибирск": (55.0084, 82.9357),
}
PROXY_CITY = {
    "Москва":"moscow",
    "Ростов-на-Дону":"rostov_on_don",
    "Краснодар":"krasnodar",
    "Санкт-Петербург":"saint_petersburg",
    "Казань":"kazan",
    "Самара":"samara",
    "Екатеринбург":"yekaterinburg",
    "Новосибирск":"novosibirsk",
}

def _proxy_credentials(city: str):
    provider=(os.getenv("OZON_PROXY_PROVIDER") or "").strip().lower()
    base=(os.getenv("OZON_PROXY_USER") or "").strip()
    password=(os.getenv("OZON_PROXY_PASS") or "").strip()
    host=(os.getenv("OZON_PROXY_HOST") or ("gate.decodo.com" if provider=="decodo" else "")).strip()
    port=int(os.getenv("OZON_PROXY_PORT") or ("7000" if provider=="decodo" else "0"))
    if not provider or not base or not password or not host or not port:
        return None
    city_slug=PROXY_CITY.get(city, re.sub(r"[^a-z0-9]+","_",city.lower()).strip("_"))
    session=re.sub(r"[^a-z0-9]","",f"{city_slug}{int(time.time())}")[-24:]
    if provider=="decodo":
        prefix=base if base.startswith("user-") else "user-"+base
        username=f"{prefix}-country-ru-city-{city_slug}-session-{session}"
    elif provider=="brightdata":
        username=f"{base}-country-ru-city-{city_slug}-session-{session}"
    else:
        tmpl=os.getenv("OZON_PROXY_USER_TEMPLATE","{user}")
        username=tmpl.format(user=base,country="ru",city=city_slug,session=session)
    return {"provider":provider,"host":host,"port":port,"username":username,"password":password}

def _proxy_extension(proxy):
    manifest={
      "manifest_version":3,
      "name":"Ozon Live Proxy",
      "version":"1.0",
      "permissions":["proxy","storage","webRequest","webRequestAuthProvider"],
      "host_permissions":["<all_urls>"],
      "background":{"service_worker":"background.js"}
    }
    bg=f"""
chrome.proxy.settings.set({{
  value: {{mode: 'fixed_servers', rules: {{singleProxy: {{scheme: 'http', host: '{proxy["host"]}', port: {proxy["port"]}}}, bypassList: ['localhost','127.0.0.1']}}}},
  scope: 'regular'
}});
chrome.webRequest.onAuthRequired.addListener(
  function(details, callback) {{ callback({{authCredentials: {{username: '{proxy["username"]}', password: '{proxy["password"]}'}}}}); }},
  {{urls: ['<all_urls>']}},
  ['asyncBlocking']
);
"""
    td=tempfile.mkdtemp(prefix="ozon-proxy-")
    p=os.path.join(td,"proxy.zip")
    with zipfile.ZipFile(p,"w") as z:
        z.writestr("manifest.json",json.dumps(manifest))
        z.writestr("background.js",bg)
    return p

def driver_new(city=None):
    o=webdriver.ChromeOptions()
    for a in ["--no-sandbox","--disable-dev-shm-usage","--disable-infobars","--lang=ru-RU","--window-size=1920,1080","--disable-blink-features=AutomationControlled"]:
        o.add_argument(a)
    o.add_experimental_option("excludeSwitches", ["enable-automation"])
    o.add_experimental_option("useAutomationExtension", False)
    proxy=_proxy_credentials(city or "")
    if proxy:
        o.add_extension(_proxy_extension(proxy))
    d=webdriver.Chrome(options=o)
    try:
        d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",{"source":"Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"})
    except Exception:
        pass
    d._ozon_proxy=proxy
    return d

def click_if(driver, xpath):
    try:
        els=driver.find_elements(By.XPATH,xpath)
        for e in els:
            if e.is_displayed() and e.is_enabled():
                driver.execute_script("arguments[0].click()",e); time.sleep(.7); return True
    except Exception:
        pass
    return False

def close_popups(driver):
    for t in ["Всё верно","Да, всё верно","Хорошо","Понятно","Принять"]:
        click_if(driver,f"//button[contains(normalize-space(.),'{t}')]")
    for xp in ["//button[@aria-label='Закрыть']","//button[@data-testid='closeButton']"]:
        click_if(driver,xp)

def set_geo(driver, city):
    latlon=CITY_COORDS.get(city)
    if latlon:
        try:
            driver.execute_cdp_cmd("Emulation.setGeolocationOverride",{"latitude":latlon[0],"longitude":latlon[1],"accuracy":50})
        except Exception:
            pass

def set_city(driver, city):
    set_geo(driver,city)
    proxy=getattr(driver,"_ozon_proxy",None)
    if proxy:
        # Residential city targeting is the authoritative geo layer.
        # We still set browser geolocation coordinates, but do not depend on Ozon's UI selector.
        driver.get("https://www.ozon.ru/")
        time.sleep(random.uniform(4,6))
        body=(driver.find_element(By.TAG_NAME,"body").text or "").lower()
        if "похоже, нет соединения" in body or "выключите vpn" in body:
            return {"ok":False,"reason":"proxy_blocked_or_not_applied"}
        close_popups(driver)
        return {"ok":True,"reason":"proxy_geo_"+proxy["provider"]}
    driver.get("https://www.ozon.ru/")
    time.sleep(random.uniform(3,5))
    close_popups(driver)

    # Open location selector. Ozon changes markup often, so use several semantic fallbacks.
    open_xpaths=[
        "//*[self::button or @role='button'][contains(normalize-space(.),'Доставка')]",
        "//*[self::button or @role='button'][contains(normalize-space(.),'город')]",
        "//*[self::button or @role='button'][contains(normalize-space(.),'адрес')]",
        "//header//*[self::button or @role='button'][string-length(normalize-space(.))>1]",
    ]
    opened=False
    for xp in open_xpaths:
        try:
            for e in driver.find_elements(By.XPATH,xp):
                txt=(e.text or "").strip().lower()
                if xp.startswith("//header") and not any(k in txt for k in ["москва","ростов","краснодар","достав","адрес","город"]):
                    continue
                if e.is_displayed():
                    driver.execute_script("arguments[0].click()",e); time.sleep(1); opened=True; break
            if opened: break
        except Exception:
            pass

    if not opened:
        diag_dir=Path("ozon_live/debug"); diag_dir.mkdir(parents=True,exist_ok=True)
        safe=re.sub(r"[^0-9A-Za-zА-Яа-я_-]+","_",city)
        try: driver.save_screenshot(str(diag_dir/f"{safe}.png"))
        except Exception: pass
        try:
            body=(driver.find_element(By.TAG_NAME,"body").text or "")[:12000]
            (diag_dir/f"{safe}_body.txt").write_text(body,encoding="utf-8")
        except Exception: pass
        try:
            candidates=[]
            for el in driver.find_elements(By.XPATH,"//button | //a | //*[@role='button']"):
                try:
                    if el.is_displayed():
                        txt=(el.text or "").strip()
                        if txt: candidates.append({"tag":el.tag_name,"text":txt[:300],"aria":el.get_attribute("aria-label") or "","testid":el.get_attribute("data-testid") or "","widget":el.get_attribute("data-widget") or ""})
                except Exception: pass
            (diag_dir/f"{safe}_clickables.json").write_text(json.dumps(candidates[:300],ensure_ascii=False,indent=2),encoding="utf-8")
        except Exception: pass
        try:
            html=driver.page_source
            (diag_dir/f"{safe}_page.html").write_text(html[:400000],encoding="utf-8")
        except Exception: pass
        return {"ok":False,"reason":"location_control_not_found","debug_prefix":str(diag_dir/f"{safe}")}

    inputs=[]
    for sel in ["input[placeholder*='город' i]","input[placeholder*='адрес' i]","input[placeholder*='населен' i]","input"]:
        try:
            inputs=[x for x in driver.find_elements(By.CSS_SELECTOR,sel) if x.is_displayed()]
            if inputs: break
        except Exception: pass
    if not inputs:
        return {"ok":False,"reason":"location_input_not_found"}

    inp=inputs[0]
    try:
        inp.clear(); inp.send_keys(city); time.sleep(2)
    except Exception:
        return {"ok":False,"reason":"location_input_failed"}

    candidates=[
        f"//*[self::button or self::div or self::li or self::span][normalize-space(.)='{city}']",
        f"//*[self::button or self::div or self::li][contains(normalize-space(.),'{city}')]",
    ]
    chosen=False
    for xp in candidates:
        try:
            for e in driver.find_elements(By.XPATH,xp):
                if e.is_displayed():
                    driver.execute_script("arguments[0].click()",e); time.sleep(2); chosen=True; break
            if chosen: break
        except Exception: pass
    if not chosen:
        return {"ok":False,"reason":"location_suggestion_not_found"}

    close_popups(driver)
    return {"ok":True,"reason":"selected"}

def collect_skus(driver):
    total=driver.execute_script("return document.body.scrollHeight")
    for f in (.2,.4,.6,.8,1):
        driver.execute_script(f"window.scrollTo(0,{int(total*f)})"); time.sleep(.5)
    driver.execute_script("window.scrollTo(0,0)"); time.sleep(.4)
    selectors=[
        '[data-widget="searchResultsV2"] a[href*="/product/"]',
        '[data-widget="searchResults"] a[href*="/product/"]',
        'a[href*="/product/"]'
    ]
    els=[]
    for s in selectors:
        els=driver.find_elements(By.CSS_SELECTOR,s)
        if els: break
    out=[]; seen=set()
    for e in els:
        href=e.get_attribute("href") or ""
        m=SKU_RE.search(href)
        if m and m.group(1) not in seen:
            seen.add(m.group(1)); out.append(m.group(1))
    return out

def scan(driver, query, sku, max_items=300):
    checked=0
    pages=max(1,(max_items+35)//36)
    for page in range(1,pages+1):
        url=SEARCH_URL.format(q=quote_plus(query),page=page)
        driver.get(url); time.sleep(random.uniform(3,5)); close_popups(driver)
        try:
            WebDriverWait(driver,25).until(EC.presence_of_element_located((By.CSS_SELECTOR,'a[href*="/product/"]')))
        except TimeoutException:
            return {"status":"blocked_or_empty","position":None,"checked":checked,"page":page}
        skus=collect_skus(driver)
        if not skus: break
        for s in skus:
            if checked>=max_items: break
            checked+=1
            if s==str(sku):
                return {"status":"ok","position":checked,"checked":checked,"page":page}
        if checked>=max_items: break
        time.sleep(random.uniform(2,4))
    return {"status":"not_found","position":None,"checked":checked,"page":None}

def run(config_path):
    cfg=json.loads(Path(config_path).read_text(encoding="utf-8"))
    results=[]
    for task in cfg["tasks"]:
        for city in task["cities"]:
            d=driver_new(city)
            try:
                loc=set_city(d,city)
                if not loc["ok"]:
                    r={"status":"city_error","position":None,"checked":0,"page":None}
                else:
                    r=scan(d,task["query"],task["sku"],task.get("max_items",300))
                results.append({
                    "captured_at":datetime.now(timezone.utc).isoformat(),
                    "city":city,
                    "article":task.get("article",""),
                    "sku":str(task["sku"]),
                    "query":task["query"],
                    "max_items":task.get("max_items",300),
                    **r,
                    "city_status":loc["reason"] if 'loc' in locals() else "unknown",
                })
            except Exception as e:
                results.append({
                    "captured_at":datetime.now(timezone.utc).isoformat(),"city":city,
                    "article":task.get("article",""),"sku":str(task["sku"]),"query":task["query"],
                    "max_items":task.get("max_items",300),"status":"error","position":None,"checked":0,"page":None,
                    "city_status":"exception","error":repr(e)[:500]
                })
            finally:
                try:d.quit()
                except Exception:pass
            time.sleep(random.uniform(5,9))
    return {"generated_at":datetime.now(timezone.utc).isoformat(),"results":results}

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--config",default="ozon_live/config.json")
    ap.add_argument("--out",default="ozon_live/results/latest.json")
    args=ap.parse_args()
    out=run(args.config)
    p=Path(args.out);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(out,ensure_ascii=False,indent=2))
