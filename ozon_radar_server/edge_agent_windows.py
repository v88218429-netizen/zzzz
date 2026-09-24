from __future__ import annotations

import json, re, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
import websocket

HOST="0.0.0.0"; PORT=8900; CDP="http://127.0.0.1:9222"; _seq=0

def _json(url):
    with urllib.request.urlopen(url, timeout=10) as r: return json.load(r)

def _target_ws():
    pages=[t for t in _json(CDP+"/json") if t.get("type")=="page"]
    if not pages: raise RuntimeError("No browser page target")
    return pages[0]["webSocketDebuggerUrl"]

def _cmd(ws, method, params=None):
    global _seq; _seq+=1; mid=_seq
    ws.send(json.dumps({"id":mid,"method":method,"params":params or {}}))
    while True:
        d=json.loads(ws.recv())
        if d.get('id')==mid:
            if 'error' in d: raise RuntimeError(str(d['error']))
            return d.get('result') or {}

def _eval(ws, expression):
    o=_cmd(ws,"Runtime.evaluate",{"expression":expression,"returnByValue":True,"awaitPromise":True})
    return (o.get('result') or {}).get('value')

def get_position(query, sku, max_position=100):
    target=str(sku); seen=[]; seen_set=set(); pages=max(1,min(10,(max_position+35)//36))
    ws=websocket.create_connection(_target_ws(), timeout=60)
    try:
        _cmd(ws,"Runtime.enable"); _cmd(ws,"Page.enable")
        def collect():
            hrefs=_eval(ws, """Array.from(document.querySelectorAll('a[href*="/product/"]')).map(a=>a.href||a.getAttribute('href')||'')""") or []
            for href in hrefs:
                m=re.search(r"-(\d{6,})(?:/|\?|$)", str(href))
                if not m: continue
                val=m.group(1)
                if val in seen_set: continue
                seen_set.add(val); seen.append(val)
                if val==target: return {"ok":True,"position":len(seen),"checked_depth":len(seen),"source":"windows-browser-cdp"}
                if len(seen)>=max_position: break
            return None
        for page_no in range(1,pages+1):
            url="https://www.ozon.ru/search/?text="+urllib.parse.quote(query)
            if page_no>1: url+=f"&page={page_no}"
            _cmd(ws,"Page.navigate",{"url":url}); time.sleep(8)
            for step in range(12):
                found=collect()
                if found: found.update(page=page_no,scroll_step=step); return found
                if len(seen)>=max_position: break
                _eval(ws,"window.scrollBy(0,1100); true"); time.sleep(1)
            found=collect()
            if found: found.update(page=page_no,scroll_step=12); return found
            if len(seen)>=max_position: break
        return {"ok":True,"position":None,"status":f"NOT_FOUND_TOP_{max_position}","checked_depth":len(seen),"source":"windows-browser-cdp"}
    finally: ws.close()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        p=urllib.parse.urlsplit(self.path)
        if p.path=="/health": self._send({"ok":True,"service":"ozon-edge-windows-agent"}); return
        if p.path!="/position": self._send({"ok":False,"error":"not found"},404); return
        q=urllib.parse.parse_qs(p.query); query=(q.get('query') or [''])[0]; sku=(q.get('sku') or [''])[0]
        try: maxp=int((q.get('max_position') or ['100'])[0])
        except: maxp=100
        if not query or not sku: self._send({"ok":False,"error":"query and sku required"},400); return
        try: self._send(get_position(query,sku,maxp),200)
        except Exception as e: self._send({"ok":False,"error":f"{type(e).__name__}: {e}"},500)
    def log_message(self,*_): pass
    def _send(self,payload,status=200):
        raw=json.dumps(payload,ensure_ascii=False).encode("utf-8"); self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)

if __name__=="__main__": HTTPServer((HOST,PORT),Handler).serve_forever()