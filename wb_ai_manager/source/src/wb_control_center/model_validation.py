from __future__ import annotations

from statistics import mean
from typing import Any

from .demand_forecast import build_demand_forecast


class DemandModelValidator:
    """Rolling one-day backtest on the seller's own SKU history."""

    def validate(self, portfolio: dict[str, Any], max_skus: int = 100, max_windows: int = 14) -> dict[str, Any]:
        products=((portfolio.get("own_27") or {}).get("products") or []) if isinstance(portfolio,dict) else []
        sku_reports=[]; all_abs=[]; total_actual=0.0; total_abs=0.0; total_bias=0.0; nall=0
        for prod in products[:max_skus]:
            if not isinstance(prod,dict): continue
            hist=[x for x in (prod.get("orders_daily_history") or []) if isinstance(x,dict) and x.get("date") is not None]
            if len(hist)<21: continue
            errs=[]; bias=[]; actual_sum=0.0
            start=max(14,len(hist)-max_windows-1)
            for i in range(start,len(hist)-1):
                train=hist[:i+1]
                try: actual=float(hist[i+1].get("orders") or 0)
                except Exception: continue
                tmp=dict(prod); tmp["orders_daily_history"]=train; tmp["search_frequency_trend_pct"]=None
                fc=build_demand_forecast(tmp)
                pred=fc.forecast_orders_1d
                if pred is None: continue
                err=abs(float(pred)-actual); errs.append(err); bias.append(float(pred)-actual); actual_sum+=actual
            if not errs: continue
            wape=(sum(errs)/actual_sum*100) if actual_sum>0 else None
            sku_reports.append({"sku":str(prod.get("sku") or ""),"name":prod.get("name"),"samples":len(errs),"mae_orders":round(mean(errs),2),"wape_pct":round(wape,2) if wape is not None else None,"bias_orders":round(mean(bias),2)})
            all_abs.extend(errs); total_actual+=actual_sum; total_abs+=sum(errs); total_bias+=sum(bias); nall+=len(errs)
        sku_reports.sort(key=lambda x:(x.get("wape_pct") is None, -(x.get("wape_pct") or 0)))
        return {
            "samples":nall,
            "skus":len(sku_reports),
            "mae_orders":round(mean(all_abs),2) if all_abs else None,
            "wape_pct":round(total_abs/total_actual*100,2) if total_actual>0 else None,
            "bias_orders":round(total_bias/nall,2) if nall else None,
            "worst_skus":sku_reports[:20],
            "status":"ok" if nall>=50 else "insufficient_history",
            "note":"Проверяется точность прогноза спроса на собственной истории. Частотность поиска не подмешивается в прошлые окна, чтобы не было утечки будущих данных.",
        }
