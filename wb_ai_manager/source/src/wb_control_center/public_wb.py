from __future__ import annotations

import asyncio
import statistics
from typing import Any
import httpx

SEARCH_URL='https://search.wb.ru/exactmatch/ru/common/v9/search'

class PublicWBClient:
    """Read-only storefront intelligence. Uses unofficial WB website endpoints.
    It is isolated because endpoints can change; failure must never block Seller API agents.
    """
    def __init__(self, min_interval: float=1.0, dest: str="-1257786"):
        self.min_interval=min_interval
        self.dest=str(dest or "").strip()
        self._lock=asyncio.Lock(); self._last=0.0
        self.client=httpx.AsyncClient(timeout=8, headers={'User-Agent':'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125 Safari/537.36'})

    async def close(self):
        await self.client.aclose()

    async def search(self, query: str, limit: int=50) -> list[dict[str,Any]]:
        async with self._lock:
            loop=asyncio.get_running_loop(); now=loop.time(); delay=self.min_interval-(now-self._last)
            if delay>0: await asyncio.sleep(delay)
            params={'ab_testing':'false','appType':'1','curr':'rub','page':1,'query':query,'resultset':'catalog','sort':'popular','spp':'30','suppressSpellcheck':'false'}
            if self.dest:
                params['dest']=self.dest
            r=await self.client.get(SEARCH_URL,params=params); self._last=loop.time()
        r.raise_for_status(); data=r.json() or {}
        products=data.get('products') or (data.get('data') or {}).get('products') or []
        out=[]
        for p in products[:limit]:
            sizes=p.get('sizes') or [{}]; pr=(sizes[0].get('price') or {}) if sizes else {}
            price=pr.get('product') or pr.get('total')
            out.append({'id':p.get('id'),'name':p.get('name'),'brand':p.get('brand'),'price_rub':price/100 if isinstance(price,(int,float)) else None,'rating':p.get('reviewRating'),'reviews':p.get('feedbacks')})
        return out

    @staticmethod
    def analyze(products:list[dict[str,Any]], own_ids:list[int]) -> dict[str,Any]:
        own_positions=[]; own_prices=[]; own_ratings=[]; own_reviews=[]
        comp_prices=[]; comp_ratings=[]; comp_reviews=[]
        for i,p in enumerate(products,1):
            try: pid=int(p.get('id'))
            except Exception: pid=0
            is_own=pid in own_ids
            if is_own:
                own_positions.append(i)
                if p.get('price_rub'): own_prices.append(float(p['price_rub']))
                if p.get('rating') is not None:
                    try: own_ratings.append(float(p['rating']))
                    except Exception: pass
                if p.get('reviews') is not None:
                    try: own_reviews.append(float(p['reviews']))
                    except Exception: pass
            else:
                if p.get('price_rub'): comp_prices.append(float(p['price_rub']))
                if p.get('rating') is not None:
                    try: comp_ratings.append(float(p['rating']))
                    except Exception: pass
                if p.get('reviews') is not None:
                    try: comp_reviews.append(float(p['reviews']))
                    except Exception: pass
        median_price=statistics.median(comp_prices) if comp_prices else None
        own_price=statistics.median(own_prices) if own_prices else None
        price_delta=((own_price/median_price-1)*100) if own_price and median_price else None
        median_rating=statistics.median(comp_ratings) if comp_ratings else None
        own_rating=statistics.median(own_ratings) if own_ratings else None
        rating_delta=(own_rating-median_rating) if own_rating is not None and median_rating is not None else None
        median_reviews=statistics.median(comp_reviews) if comp_reviews else None
        own_review_count=statistics.median(own_reviews) if own_reviews else None
        return {
            'own_position':min(own_positions) if own_positions else None,
            'own_price_rub':own_price, 'competitor_median_price_rub':median_price,
            'price_vs_median_pct':price_delta, 'competitor_count':len(comp_prices),
            'own_rating':own_rating, 'competitor_median_rating':median_rating,
            'rating_vs_median':rating_delta, 'own_review_count':own_review_count,
            'competitor_median_reviews':median_reviews,
        }
