from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import re
from typing import Any

DEFAULT_FRESH_HOURS={"position":48.0,"traffic":48.0,"ads":36.0,"bid":36.0,"orders":72.0,"frequency":168.0}
QUERY_KEYS=("query_intelligence","search_core","search_queries","queries")
STOPWORDS={"для","и","в","на","с","из","по","к","от","до","под","над","the","a","an","and","for","with","of","to"}

def _num(v:Any)->float|None:
    if v is None or v=="": return None
    if isinstance(v,bool): return float(v)
    if isinstance(v,(int,float)):
        x=float(v); return x if math.isfinite(x) else None
    s=re.sub(r"[^0-9+\-.]","",str(v).strip().replace("\u00a0","").replace(" ","").replace(",","."))
    if s in {"","+","-",".","+.","-."}: return None
    try:
        x=float(s); return x if math.isfinite(x) else None
    except ValueError: return None

def _first_num(m:dict[str,Any],*keys:str)->float|None:
    for k in keys:
        if k in m:
            v=_num(m.get(k))
            if v is not None: return v
    return None

def _text(m:dict[str,Any],*keys:str)->str|None:
    for k in keys:
        v=m.get(k)
        if v is not None and str(v).strip(): return str(v).strip()
    return None

def _dt(v:Any)->datetime|None:
    if not v: return None
    if isinstance(v,datetime): d=v
    else:
        s=str(v).strip(); d=None
        for fmt in ("%d.%m.%Y %H:%M:%S","%d.%m.%Y %H:%M","%d.%m.%Y","%Y-%m-%d"):
            try: d=datetime.strptime(s,fmt); break
            except ValueError: pass
        if d is None:
            try: d=datetime.fromisoformat(s.replace("Z","+00:00"))
            except ValueError: return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)

def _norm(v:str)->str:
    return re.sub(r"\s+"," ",re.sub(r"[^0-9a-zа-яё]+"," ",v.lower(),flags=re.I)).strip()

def _tokens(v:str)->frozenset[str]:
    return frozenset(x for x in _norm(v).split() if x not in STOPWORDS and len(x)>1)

def _rows(v:Any)->list[dict[str,Any]]:
    if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
    if isinstance(v,dict):
        for k in ("rows","queries","items","data"):
            if isinstance(v.get(k),list): return [x for x in v[k] if isinstance(x,dict)]
        if v and all(isinstance(x,dict) for x in v.values()): return list(v.values())
    return []

@dataclass(frozen=True)
class QueryManagerConfig:
    target_paid_share:float=0.45
    high_paid_share:float=0.55
    max_bid_change_pct:float=15.0
    absolute_bid_cap_rub:float=2000.0
    auto_execute_bid_change_pct:float=0.0
    fresh_hours:dict[str,float]|None=None
    def freshness_limits(self)->dict[str,float]:
        out=dict(DEFAULT_FRESH_HOURS)
        if self.fresh_hours: out.update({k:float(v) for k,v in self.fresh_hours.items()})
        return out

class QueryManager:
    """Canonical query fact + decision layer. Missing/stale facts block monetary actions."""
    def __init__(self,config:QueryManagerConfig|None=None,now:datetime|None=None):
        self.config=config or QueryManagerConfig()
        self.now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    def build(self,portfolio:dict[str,Any],snapshots:dict[str,Any]|None=None)->dict[str,Any]:
        own=(portfolio or {}).get("own_27") or {}
        cards=[]
        for p in own.get("products") or []:
            if isinstance(p,dict): cards.extend(self._product_cards(p))
        self._overlay_runtime_search(cards, snapshots or {})
        self._rank(cards); self._clusters(cards)
        for c in cards: self._finish(c)
        qr={"protect_top":0,"fix_gap":1,"avoid_overbuy":2,"refresh_fact":3,"observe":4}
        cards.sort(key=lambda x:(qr.get(str(x.get("queue")),9),-float(x.get("opportunity_score") or 0),str(x.get("sku")),str(x.get("query"))))
        summary=self._summary(cards)
        return {"generated_at":self.now.isoformat(),"status":summary["query_status"],"summary":summary,
                "cards":cards,"plans_by_sku":self._plans(cards),"operator_brief":self._brief(cards,summary)}

    def _overlay_runtime_search(self,cards:list[dict[str,Any]],snapshots:dict[str,Any])->None:
        """Overlay only genuinely fresh WB search facts collected by the live runtime.

        The Sheets query layer remains the source of the monitored query universe and
        frequency. Runtime search_positions can refresh position timestamps, but only
        when the agent actually obtained WB search-report data. Its trusted-Sellmonitor
        fallback is intentionally ignored here so an old sheet row can never be made
        fresh merely because it was re-read during a new runtime cycle.
        """
        agent=(snapshots or {}).get("search_positions") or {}
        item=agent.get("positions") if isinstance(agent,dict) else None
        if not isinstance(item,dict):
            return
        observed_at=_dt(item.get("created_at"))
        data=item.get("data")
        if observed_at is None or not isinstance(data,dict):
            return

        exact:dict[tuple[str,str],dict[str,Any]]={}
        per_sku:dict[str,list[dict[str,Any]]]={}
        for row in data.values():
            if not isinstance(row,dict):
                continue
            if str(row.get("source") or "")!="wb_search_report":
                continue
            sku=str(row.get("nm_id") or row.get("nmId") or "").strip()
            pos=_num(row.get("position"))
            if not sku.isdigit() or pos is None:
                continue
            query=str(row.get("query") or "").strip()
            record={"position":pos,"query":query}
            per_sku.setdefault(sku,[]).append(record)
            if query:
                exact[(sku,_norm(query))]=record

        for card in cards:
            sku=str(card.get("sku") or "").strip()
            q=_norm(str(card.get("query") or ""))
            hit=exact.get((sku,q))
            if hit is None:
                # A query-less WB row is product-level position, not evidence for a
                # specific monitored phrase. Never spread it across multiple queries.
                continue
            card["position"]=hit["position"]
            card["runtime_search_source"]="wb_search_report"
            card["runtime_search_observed_at"]=observed_at.isoformat()
            ts=dict(card.get("timestamps") or {})
            ts["position"]=observed_at.isoformat()
            card["timestamps"]=ts
            fresh=dict(card.get("freshness") or {})
            fresh["position"]=self._fresh("position",observed_at)
            card["freshness"]=fresh

    def _product_cards(self,p:dict[str,Any])->list[dict[str,Any]]:
        rows=[]; source="legacy_top_query"
        for key in QUERY_KEYS:
            cand=_rows(p.get(key))
            if cand: rows=cand; source=key; break
        if not rows and _text(p,"top_search_query"):
            rows=[{"query":p.get("top_search_query"),"frequency":p.get("top_search_frequency"),
                   "position":p.get("top_search_position"),"target_position":p.get("top_search_target_position"),
                   "snapshot":p.get("search_snapshot_at")}]
        sku=_text(p,"sku","nm_id","nmId","nmID") or ""
        article=_text(p,"seller_article","name","article","vendor_code")
        out=[]; seen=set()
        for r in rows:
            q=_text(r,"query","search_query","phrase","keyword","Поисковый запрос") or ""
            n=_norm(q)
            if not n or n in seen: continue
            seen.add(n); out.append(self._base(p,r,sku,article,q,source))
        return out

    def _base(self,p:dict[str,Any],r:dict[str,Any],sku:str,article:str|None,q:str,source:str)->dict[str,Any]:
        freq=_first_num(r,"frequency","search_frequency","frequency_current","Частотность")
        pos=_first_num(r,"position","current_position","search_position","Текущая позиция")
        target=_first_num(r,"target_position","target","effective_target","Эффективная цель")
        org=_first_num(r,"organic_clicks","organic_traffic_clicks","organic_clicks_current")
        paid=_first_num(r,"paid_clicks","ad_clicks","advertising_clicks","promo_clicks")
        clicks=_first_num(r,"clicks","traffic_clicks","clicks_total")
        if clicks is None and org is not None and paid is not None: clicks=org+paid
        imp=_first_num(r,"impressions","views","shows")
        orders=_first_num(r,"orders","order_count","orders_count")
        carts=_first_num(r,"carts","cart_count","basket_count")
        ctr=_first_num(r,"ctr_pct","ctr","click_through_rate_pct")
        if ctr is None and clicks is not None and imp and imp>0: ctr=clicks/imp*100
        cr=_first_num(r,"cr_pct","conversion_rate_pct","order_cr_pct")
        if cr is None and orders is not None and clicks and clicks>0: cr=orders/clicks*100
        spend=_first_num(r,"ad_spend_rub","spend_rub","advertising_spend_rub","spend")
        cpc=_first_num(r,"cpc_rub","cpc","cost_per_click_rub")
        if cpc is None and spend is not None and paid and paid>0: cpc=spend/paid
        drr=_first_num(r,"drr_pct","fact_drr_pct","advertising_drr_pct")
        cid=_text(r,"campaign_id","advert_id","advertId","campaign")
        bid=_first_num(r,"current_search_bid_rub","current_bid_rub","search_bid_rub","bid_rub","bid")
        ts=self._timestamps(p,r)
        return {"sku":sku,"seller_article":article,"query":q,"normalized_query":_norm(q),"source":source,
                "frequency":freq,"position":pos,"target_position":target,"organic_clicks":org,"paid_clicks":paid,
                "clicks":clicks,"impressions":imp,"carts":carts,"orders":orders,
                "ctr_pct":round(ctr,3) if ctr is not None else None,"cr_pct":round(cr,3) if cr is not None else None,
                "cpc_rub":round(cpc,2) if cpc is not None else None,"ad_spend_rub":round(spend,2) if spend is not None else None,
                "drr_pct":round(drr,3) if drr is not None else None,"campaign_id":cid,"current_search_bid_rub":bid,
                "timestamps":{k:(v.isoformat() if v else None) for k,v in ts.items()},
                "freshness":{k:self._fresh(k,v) for k,v in ts.items()},
                "traffic_split":self._traffic(org,paid,clicks),"economics":self._economics(p,r),
                "query_cache_status":r.get("query_cache_status") or p.get("query_cache_status"),
                "query_fallback_source":r.get("query_fallback_source") or p.get("query_fallback_source")}

    def _timestamps(self,p:dict[str,Any],r:dict[str,Any])->dict[str,datetime|None]:
        generic=_text(r,"snapshot_at","snapshot","date","Дата снимка") or _text(p,"search_snapshot_at")
        return {
            "position":_dt(_text(r,"position_at","position_snapshot_at") or generic),
            "frequency":_dt(_text(r,"frequency_at","frequency_snapshot_at") or generic),
            "traffic":_dt(_text(r,"traffic_at","traffic_snapshot_at")),
            "ads":_dt(_text(r,"ads_at","ad_snapshot_at","advertising_at")),
            "bid":_dt(_text(r,"bid_at","bid_snapshot_at")),
            "orders":_dt(_text(r,"orders_at","orders_snapshot_at")),
        }

    def _fresh(self,fact:str,v:datetime|None)->dict[str,Any]:
        if v is None: return {"status":"missing","age_hours":None,"at":None}
        age=max(0.0,(self.now-v).total_seconds()/3600)
        lim=self.config.freshness_limits().get(fact,48.0)
        return {"status":"fresh" if age<=lim else "stale","age_hours":round(age,1),"max_age_hours":lim,"at":v.isoformat()}

    def _economics(self,p:dict[str,Any],r:dict[str,Any])->dict[str,Any]:
        price=_first_num(p,"price_rub","price_after_discount_rub","price_client_rub")
        profit=_first_num(p,"profit_rub","plan_profit_rub","profit_per_unit_rub")
        margin=_first_num(p,"margin_pct","plan_margin_pct")
        plan=_first_num(p,"plan_drr_pct","target_drr_pct")
        fact=_first_num(r,"drr_pct","fact_drr_pct")
        if fact is None: fact=_first_num(p,"fact_drr_sales_pct","actual_drr_pct")
        be=(plan+profit/price*100) if price and price>0 and profit is not None and plan is not None else None
        max_order=price*plan/100 if price and plan is not None else None
        profitable=None
        if fact is not None and be is not None: profitable=fact<be
        elif plan is not None and be is not None: profitable=plan<be
        return {"price_rub":price,"profit_rub":profit,"margin_pct":margin,"plan_drr_pct":plan,"fact_drr_pct":fact,
                "break_even_drr_pct":round(be,3) if be is not None else None,
                "max_ad_cost_per_order_rub":round(max_order,2) if max_order is not None else None,
                "profitable_to_scale":profitable}

    def _traffic(self,org:float|None,paid:float|None,total:float|None)->dict[str,Any]:
        target=round(self.config.target_paid_share*100,1)
        if org is None or paid is None:
            return {"status":"unknown","organic_share_pct":None,"paid_share_pct":None,"target_paid_share_pct":target,"excess_paid_clicks":None}
        total=total if total is not None else org+paid
        if total<=0: return {"status":"no_traffic","organic_share_pct":0.0,"paid_share_pct":0.0,"target_paid_share_pct":target,"excess_paid_clicks":0.0}
        ps=paid/total; os=org/total
        return {"status":"overpaid" if ps>self.config.high_paid_share else "balanced",
                "organic_share_pct":round(os*100,2),"paid_share_pct":round(ps*100,2),"target_paid_share_pct":target,
                "excess_paid_clicks":round(max(0.0,paid-total*self.config.target_paid_share),2)}

    def _rank(self,cards:list[dict[str,Any]])->None:
        groups={}
        for c in cards: groups.setdefault(str(c.get("sku") or ""),[]).append(c)
        for rows in groups.values():
            ranked=sorted(rows,key=lambda x:float(x.get("frequency") or 0),reverse=True)
            total=sum(float(x.get("frequency") or 0) for x in ranked)
            for i,c in enumerate(ranked,1):
                c["frequency_rank"]=i
                c["frequency_share_pct"]=round(float(c.get("frequency") or 0)/total*100,2) if total>0 else 0.0

    def _clusters(self,cards:list[dict[str,Any]])->None:
        by={}
        for c in cards: by.setdefault(str(c.get("sku") or ""),[]).append(c)
        for sku,rows in by.items():
            clusters:list[tuple[frozenset[str],str]]=[]
            for c in sorted(rows,key=lambda x:-float(x.get("frequency") or 0)):
                t=_tokens(str(c.get("query") or "")); selected=None
                for base,cid in clusters:
                    u=len(t|base); score=len(t&base)/u if u else 0
                    if t and base and score>=0.60: selected=cid; break
                if selected is None:
                    selected=f"{sku}:q{len(clusters)+1}"; clusters.append((t,selected))
                c["cluster_id"]=selected

    def _finish(self,c:dict[str,Any])->None:
        blockers=self._blockers(c); c["blockers"]=blockers
        c["freshness_status"]="fresh" if not blockers else "needs_refresh"
        role=self._role(c); c["role"]=role
        c.update(self._opportunity(c,role,blockers))
        bid=self._bid(c,role,blockers); c["bid_decision"]=bid
        c["execution_mode"]=self._execution(bid,blockers); c["control_checkpoints"]=self._controls(bid)

    def _blockers(self,c:dict[str,Any])->list[str]:
        f=c.get("freshness") or {}; out=[]
        for fact in ("frequency","position"):
            if c.get(fact) is None: out.append(f"missing_{fact}")
            else:
                st=(f.get(fact) or {}).get("status")
                if st=="stale": out.append(f"stale_{fact}")
                elif st=="missing": out.append(f"missing_{fact}_timestamp")
        return list(dict.fromkeys(out))

    def _role(self,c:dict[str,Any])->str:
        rank=int(c.get("frequency_rank") or 999); share=_num(c.get("frequency_share_pct")) or 0
        cr=_num(c.get("cr_pct")); pos=_num(c.get("position")); target=_num(c.get("target_position")); orders=_num(c.get("orders")) or 0
        if rank<=5 or share>=10: return "core"
        if cr is not None and cr>=5 and (pos is None or target is None or pos>target): return "growth"
        if orders<=0 and rank>20 and (cr is None or cr<1): return "test"
        if pos is not None and target is not None and pos<=target: return "hold"
        return "observe"

    def _opportunity(self,c:dict[str,Any],role:str,blockers:list[str])->dict[str,Any]:
        if blockers: return {"queue":"refresh_fact","opportunity":"refresh_fact","opportunity_score":0.0}
        pos=_num(c.get("position")); target=_num(c.get("target_position")); cr=_num(c.get("cr_pct")); freq=_num(c.get("frequency")) or 0
        paid=_num((c.get("traffic_split") or {}).get("paid_share_pct")); profitable=(c.get("economics") or {}).get("profitable_to_scale")
        score=min(100,math.log10(freq+1)*18) if freq>0 else 0
        if pos is not None and target is not None and pos>target: score+=min(30,(pos-target)*1.5)
        if cr is not None: score+=min(20,cr*1.5)
        if profitable is False: score=min(score,20)
        if paid is not None and paid>self.config.high_paid_share*100: q,o="avoid_overbuy","paid_share_above_55"
        elif role=="core" and pos is not None and target is not None and pos<=target: q,o="protect_top","protect_core_position"
        elif role in {"core","growth"} and pos is not None and target is not None and pos>target and profitable is not False: q,o="fix_gap","position_gap"
        elif cr is not None and cr>=5 and (c.get("paid_clicks") is None or float(c.get("paid_clicks") or 0)<10): q,o="fix_gap","high_cr_low_paid_traffic"
        else: q,o="observe","no_safe_change"
        return {"queue":q,"opportunity":o,"opportunity_score":round(score,2)}

    def _bid(self,c:dict[str,Any],role:str,blockers:list[str])->dict[str,Any]:
        current=_num(c.get("current_search_bid_rub")); cid=c.get("campaign_id")
        if blockers: return self._bp("REFRESH_FACT",current,current,"Обновить факты до изменения ставки.",False)
        if not cid or current is None: return self._bp("HOLD",current,current,"Нет подтверждённой связки query → campaign → bid.",False)
        if ((c.get("freshness") or {}).get("bid") or {}).get("status")!="fresh":
            return self._bp("REFRESH_FACT",current,current,"Текущая ставка не подтверждена свежим снимком.",False)
        econ=c.get("economics") or {}; profitable=econ.get("profitable_to_scale")
        fact=_num(econ.get("fact_drr_pct")); be=_num(econ.get("break_even_drr_pct"))
        traffic=c.get("traffic_split") or {}; pos=_num(c.get("position")); target=_num(c.get("target_position"))
        pct=0.0; decision="HOLD"; reason="Нет достаточного основания менять ставку."
        if profitable is False or (fact is not None and be is not None and fact>=be):
            pct=-self.config.max_bid_change_pct; decision="DECREASE"; reason="ДРР достиг/превысил экономический потолок."
        elif traffic.get("status")=="overpaid":
            pct=-min(10.0,self.config.max_bid_change_pct); decision="DECREASE"; reason="Платный трафик выше 55%; снижаем перепокупку органики."
        elif role in {"core","growth"} and pos is not None and target is not None and pos>target+2 and profitable is True:
            pct=min(10.0,self.config.max_bid_change_pct); decision="INCREASE"; reason="Подтверждённая экономика допускает рост, позиция хуже целевой."
        elif role in {"core","growth"} and pos is not None and target is not None and pos>target+2 and profitable is None:
            decision="HOLD"; reason="Рост ставки заблокирован: нет подтверждённого экономического запаса."
        elif role=="core" and pos is not None and target is not None and pos<=target:
            reason="Ядро уже в целевой позиции; лишний рост ставки не нужен."
        target_bid=max(0.0,round(min(current*(1+pct/100),self.config.absolute_bid_cap_rub)/10)*10)
        if abs(target_bid-current)<0.01: decision="HOLD"; pct=0.0
        return self._bp(decision,current,target_bid,reason,decision in {"INCREASE","DECREASE"})

    def _bp(self,d:str,current:float|None,target:float|None,reason:str,monetary:bool)->dict[str,Any]:
        ch=(target/current-1)*100 if current not in (None,0) and target is not None else None
        return {"decision":d,"current_bid_rub":current,"target_bid_rub":target,"change_pct":round(ch,2) if ch is not None else None,"reason":reason,"monetary":monetary}

    def _execution(self,bid:dict[str,Any],blockers:list[str])->str:
        if blockers or bid.get("decision")=="REFRESH_FACT": return "INFORMATION_ONLY"
        if bid.get("monetary"):
            ch=abs(_num(bid.get("change_pct")) or 0)
            if self.config.auto_execute_bid_change_pct>0 and ch<=self.config.auto_execute_bid_change_pct: return "AUTO_EXECUTE"
            return "NEED_APPROVAL"
        return "INFORMATION_ONLY"

    def _controls(self,bid:dict[str,Any])->list[dict[str,Any]]:
        if bid.get("decision") not in {"INCREASE","DECREASE"}: return []
        return [{"after_days":d,"check_at":(self.now+timedelta(days=d)).isoformat(),
                 "metrics":["position","ctr_pct","cr_pct","drr_pct","orders","paid_share_pct"]} for d in (1,3,7)]

    def _summary(self,cards:list[dict[str,Any]])->dict[str,Any]:
        qs=Counter(str(x.get("queue") or "unknown") for x in cards); rs=Counter(str(x.get("role") or "unknown") for x in cards)
        blockers=sum(1 for x in cards if x.get("blockers"))
        ready=sum(1 for x in cards if (x.get("bid_decision") or {}).get("monetary") and not x.get("blockers"))
        stale=qs.get("refresh_fact",0)
        fresh=max(0,len(cards)-blockers)
        # Sellmonitor positionObservedAt is a per-query fact. A source refresh can
        # therefore legitimately return a mixture of fresh and old observations.
        # Old rows stay quarantined in refresh_fact and can never reach a monetary
        # action. Do not mark the whole manager unhealthy when a fresh decisionable
        # subset exists; expose the partial freshness explicitly instead.
        if not cards:
            query_status="no_data"
            facts_status="no_data"
        elif fresh <= 0:
            query_status="needs_refresh"
            facts_status="blocked"
        elif blockers:
            query_status="ready_guarded"
            facts_status="partial_refresh"
        else:
            query_status="ready"
            facts_status="all_fresh"
        return {"query_rows":len(cards),"fresh_query_rows":fresh,"blocked_query_rows":blockers,
                "refresh_fact":stale,"observe":qs.get("observe",0),"protect_top":qs.get("protect_top",0),
                "fix_gap":qs.get("fix_gap",0),"avoid_overbuy":qs.get("avoid_overbuy",0),"ready_query_actions":ready,
                "data_blockers":blockers,"query_status":query_status,"facts_status":facts_status,
                "operational_ready":bool(cards and fresh>0),"roles":dict(rs)}

    def _plans(self,cards:list[dict[str,Any]])->dict[str,list[dict[str,Any]]]:
        out={}
        for c in cards:
            bid=c.get("bid_decision") or {}
            out.setdefault(str(c.get("sku") or ""),[]).append({
                "query":c.get("query"),"role":c.get("role"),"cluster_id":c.get("cluster_id"),"campaign_id":c.get("campaign_id"),
                "current_search_bid_rub":bid.get("current_bid_rub"),"target_search_bid_rub":bid.get("target_bid_rub"),
                "decision":bid.get("decision"),"execution_mode":c.get("execution_mode"),"target_position":c.get("target_position"),
                "control_checkpoints":c.get("control_checkpoints")})
        return out

    def _brief(self,cards:list[dict[str,Any]],s:dict[str,Any])->dict[str,Any]:
        tasks=[self._task(c) for c in cards if c.get("queue") in {"protect_top","fix_gap","avoid_overbuy"}][:10]
        blockers=[{"sku":c.get("sku"),"query":c.get("query"),"blockers":c.get("blockers")} for c in cards if c.get("blockers")][:10]
        controls=[{"sku":c.get("sku"),"query":c.get("query"),"checkpoints":c.get("control_checkpoints")} for c in cards if c.get("control_checkpoints")][:10]
        return {"headline":f"Сделать {len(tasks)} задач, добить {s['data_blockers']} блокеров данных, проверить {len(controls)} контрольных точек.",
                "query_rows":s["query_rows"],"query_status":s["query_status"],"query_summary":s,"tasks":tasks,"data_blockers":blockers,"controls":controls}

    def _task(self,c:dict[str,Any])->dict[str,Any]:
        bid=c.get("bid_decision") or {}
        return {"sku":c.get("sku"),"query":c.get("query"),"queue":c.get("queue"),"campaign_id":c.get("campaign_id"),
                "current_search_bid_rub":bid.get("current_bid_rub"),"target_search_bid_rub":bid.get("target_bid_rub"),
                "decision":bid.get("decision"),"reason":bid.get("reason"),"execution_mode":c.get("execution_mode")}

def evaluate_query_outcome(before:dict[str,Any],after:dict[str,Any])->dict[str,Any]:
    """1/3/7-day evaluator. Correlation is recorded; causality is never invented."""
    bp=_num(before.get("position")); ap=_num(after.get("position"))
    bd=_num(before.get("drr_pct")); ad=_num(after.get("drr_pct"))
    bo=_num(before.get("orders")); ao=_num(after.get("orders"))
    pos_delta=(bp-ap) if bp is not None and ap is not None else None
    drr_delta=(ad-bd) if bd is not None and ad is not None else None
    orders_delta=(ao-bo) if bo is not None and ao is not None else None
    evidence=sum(x is not None for x in (pos_delta,drr_delta,orders_delta))
    if evidence<2: status="inconclusive"
    elif (pos_delta or 0)>0 and (drr_delta is None or drr_delta<=0): status="success"
    elif (drr_delta or 0)>2 and (orders_delta or 0)<=0: status="fail"
    else: status="inconclusive"
    return {"status":status,"position_improvement":pos_delta,"drr_delta_pp":drr_delta,"orders_delta":orders_delta,
            "causality":"not_proven","next_action":"keep" if status=="success" else ("rollback_or_reduce" if status=="fail" else "collect_more_data")}
