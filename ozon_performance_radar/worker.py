import os, time, json, requests, math
BASE="https://api-performance.ozon.ru"
CID=os.getenv("OZON_PERF_CLIENT_ID") or os.getenv("OZON_PERFORMANCE_CLIENT_ID") or os.getenv("PERFORMANCE_CLIENT_ID")
SECRET=os.getenv("OZON_PERF_CLIENT_SECRET") or os.getenv("OZON_PERFORMANCE_CLIENT_SECRET") or os.getenv("PERFORMANCE_CLIENT_SECRET")
STORE=os.getenv("OZON_STORE","UNKNOWN")
TARGET=os.getenv("TEST_SKU","5094364543")
LOW=float(os.getenv("LOW_RESERVE_PCT","5"))
SPIKE=float(os.getenv("COMPETITION_SPIKE_PCT","20"))

class Perf:
    def __init__(self):
        self.token=None
    def auth(self):
        r=requests.post(BASE+"/api/client/token",json={"client_id":CID,"client_secret":SECRET,"grant_type":"client_credentials"},timeout=20)
        if r.status_code >= 400:
            print("AUTH_FAILED", STORE, r.status_code, r.text[:500])
            r.raise_for_status()
        self.token=r.json()["access_token"]
        print("AUTH_OK", STORE)
    def get(self,path,params=None):
        if not self.token:self.auth()
        for attempt in range(4):
            r=requests.get(BASE+path,headers={"Authorization":"Bearer "+self.token,"Accept":"application/json"},params=params,timeout=30)
            if r.status_code==401:
                self.auth()
                continue
            if r.status_code==429:
                time.sleep(1.5*(attempt+1))
                continue
            if r.status_code>=400:
                print("GET_FAILED",STORE,path,r.status_code,r.text[:1000])
            r.raise_for_status()
            return r.json()
        r.raise_for_status()
    def campaigns(self):
        j=self.get("/api/client/campaign",{"page":1,"pageSize":100,"advObjectType":"SKU"})
        arr=j.get("list") or []
        for c in arr:
            print("CAMPAIGN_META",STORE,json.dumps({k:c.get(k) for k in ["id","title","state","paymentType","placement","productAutopilotStrategy","productCampaignMode"]},ensure_ascii=False))
        return arr
    def products(self,cid):
        out=[]; page=1
        while True:
            j=self.get(f"/api/client/campaign/{cid}/v2/products",{"page":page,"pageSize":100})
            arr=j.get("products") or []
            out+=arr
            if len(arr)<100: break
            page+=1
            time.sleep(0.4)
        return out
    def competitive(self,cid,skus):
        if not skus:return {}
        out={}
        for i in range(0,len(skus),200):
            j=self.get(f"/api/client/campaign/{cid}/products/bids/competitive",[("skus",x) for x in skus[i:i+200]])
            for x in j.get("bids") or []: out[str(x.get("sku"))]=num(x.get("bid"))
        return out

def num(v):
    try:return float(str(v).replace(",","."))
    except:return None

def main():
    if not CID or not SECRET:
        print("PERFORMANCE_CREDENTIALS=missing"); return 2
    p=Perf(); camps=p.campaigns(); print("STORE",STORE,"ACTIVE_CAMPAIGNS",len(camps))
    found=[]
    for c in camps:
        cid=str(c.get("id"))
        try:
            prods=p.products(cid)
        except requests.HTTPError as e:
            print("CAMPAIGN_SKIP",STORE,cid,str(e))
            continue
        skus=[str(x.get("sku")) for x in prods if x.get("sku") is not None]
        cb=p.competitive(cid,skus)
        for x in prods:
            sku=str(x.get("sku"))
            if sku==TARGET:
                rec={"store":STORE,"campaignId":cid,"campaign":c.get("title"),"sku":sku,"title":x.get("title"),"bid":num(x.get("bid")),"competitiveBid":cb.get(sku),"topPosition":x.get("topPosition"),"targetCir":x.get("targetCir"),"ts":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}
                found.append(rec)
    print("STORE",STORE,"TARGET_FOUND",len(found))
    for r in found: print("PERF_RESULT",json.dumps(r,ensure_ascii=False))
    return 0 if found else 3
if __name__=="__main__": raise SystemExit(main())
