from __future__ import annotations

from .base import BaseAgent
from ..config import WatchQuery, load_watch_queries
from ..models import AgentResult
from ..portfolio import PortfolioService
from ..public_wb import PublicWBClient


def _auto_queries(ctx) -> list[WatchQuery]:
    p=PortfolioService(ctx.settings).snapshot(); out=[]; seen=set()
    for x in (p.get('own_27') or {}).get('products',[]):
        name=str(x.get('name') or '').strip(); sku=x.get('sku')
        if not name or not str(sku).isdigit(): continue
        # Remove bundle noise but keep a useful buyer-like phrase.
        q=name.replace('  ',' ').strip()
        if len(q)<4 or q.lower() in seen: continue
        seen.add(q.lower()); out.append(WatchQuery(text=q, own_nm_ids=[int(sku)]))
        if len(out)>=12: break
    return out

class CompetitorsAgent(BaseAgent):
    name='competitors'
    async def run(self)->AgentResult:
        out=AgentResult(agent=self.name)
        queries=load_watch_queries() or _auto_queries(self.ctx)
        if not queries: return out
        if not self.ctx.settings.enable_public_wb_search:
            out.events.append(self.event('info','public_search_disabled','Веб-разведка WB выключена','Для автоматического рыночного контекста включи ENABLE_PUBLIC_WB_SEARCH=true. Это только чтение публичной выдачи.'))
            return out
        client=PublicWBClient(dest=self.ctx.settings.public_wb_dest)
        consecutive_failures=0
        try:
            for q in queries:
                try:
                    products=await client.search(q.text,50)
                    consecutive_failures=0
                    analysis=client.analyze(products,q.own_nm_ids)
                    payload={'query':q.text,'search_dest':client.dest or None,'products':products,'analysis':analysis}
                    out.snapshots.append((q.text,payload))
                    pos=analysis.get('own_position'); delta=analysis.get('price_vs_median_pct')
                    if pos and pos>20:
                        detail=f'Позиция ~{pos}' + (f', цена {delta:+.1f}% к медиане выдачи' if delta is not None else '')
                        out.events.append(self.event('warning',f'market_visibility:{q.text}','Слабая видимость в публичной выдаче',f'«{q.text}»: {detail}. Decision Engine проверит цену, экономику, остаток и рекламу вместе.',analysis))
                except Exception as e:
                    consecutive_failures += 1
                    out.events.append(self.event('info',f'competitor_failed:{q.text}','Веб-разведка временно недоступна',f'«{q.text}»: {e}'))
                    if consecutive_failures >= 2:
                        out.events.append(self.event('info','public_search_degraded','Публичная выдача временно отключена для этого прогона','Два запроса подряд завершились ошибкой; остальные запросы пропущены, чтобы не задерживать основной аудит.'))
                        break
        finally:
            await client.close()
        return out
