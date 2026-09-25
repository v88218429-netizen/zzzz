from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

from .config import Settings, project_root
from .policy import READ_ONLY_BUILD
from .cohort import analyze_order_lifecycle


class PortfolioService:
    """Read-only portfolio facts from trusted Sheets snapshots.

    v0.5 deliberately separates this from the WB action path. A Sheets bridge can only
    return whitelisted ranges; it cannot change WB and it cannot change Google Sheets.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = project_root()
        self.seed_path = self.root / "data" / "portfolio_seed.json"
        self.registry_path = self.root / "config" / "store_registry.yaml"
        self.live_path = settings.data_path / "portfolio_live.json"

    def registry(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            return {"version": 1, "sources": {}}
        return yaml.safe_load(self.registry_path.read_text(encoding="utf-8")) or {"version": 1, "sources": {}}

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else None
        except Exception:
            return None

    def snapshot(self) -> dict[str, Any]:
        live = self._read_json(self.live_path) if self.live_path.exists() else None
        if live:
            live["data_origin"] = live.get("mode") or "live_sheets"
            return live
        seed = self._read_json(self.seed_path) or {}
        seed = deepcopy(seed)
        seed["data_origin"] = "seeded_real_facts"
        return seed

    def connections(self) -> dict[str, Any]:
        registry = self.registry()
        sources = []
        for source_id, cfg in (registry.get("sources") or {}).items():
            sources.append(
                {
                    "id": source_id,
                    "title": cfg.get("title", source_id),
                    "role": cfg.get("role"),
                    "priority": cfg.get("priority", 0),
                    "spreadsheet_id": cfg.get("spreadsheet_id", ""),
                    "ranges": list((cfg.get("ranges") or {}).keys()),
                }
            )
        return {
            "wb": {
                "configured": bool(self.settings.wb_api_token),
                "mode": self.settings.wb_mode,
                "read_only": bool(READ_ONLY_BUILD or self.settings.force_read_only),
            },
            "wb_worker": {
                "configured": bool(self.settings.wb_worker_base_url and self.settings.wb_worker_export_token),
                "base_url_set": bool(self.settings.wb_worker_base_url),
                "export_token_set": bool(self.settings.wb_worker_export_token),
                "read_only": True,
            },
            "google_sheets": {
                "configured": bool((self.settings.google_sheets_bridge_url and self.settings.google_sheets_bridge_key) or self.live_path.exists()),
                "mode": "auto_browser_session" if self.live_path.exists() else ("apps_script_bridge" if self.settings.google_sheets_bridge_url else "not_connected"),
                "bridge_url_set": bool(self.settings.google_sheets_bridge_url),
                "bridge_key_set": bool(self.settings.google_sheets_bridge_key),
                "last_live_snapshot": self._read_json(self.live_path).get("generated_at") if self.live_path.exists() and self._read_json(self.live_path) else None,
            },
            "sources": sorted(sources, key=lambda x: (-int(x.get("priority") or 0), x["title"])),
        }

    @staticmethod
    def _num(value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        if value is None:
            return None
        s = str(value).replace("\u00a0", " ").replace("₽", "").replace("р.", "").replace("%", "").strip()
        s = s.replace(" ", "").replace(",", ".")
        try:
            return float(s)
        except Exception:
            return None


    @staticmethod
    def _as_date(value: Any) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        for candidate in (text[:10], text):
            try:
                return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date()
            except Exception:
                pass
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(text[:10], fmt).date()
            except Exception:
                pass
        return None

    @staticmethod
    def _pct_change(now: float | None, base: float | None) -> float | None:
        if now is None or base is None or base <= 0:
            return None
        return (now / base - 1.0) * 100.0

    @staticmethod
    def _range_values(payload: dict[str, Any], source: str, range_key: str) -> list[list[Any]]:
        try:
            values = payload["sources"][source]["ranges"][range_key]["values"]
            return values if isinstance(values, list) else []
        except Exception:
            return []

    def _parse_total_row(self, values: list[list[Any]]) -> dict[str, Any] | None:
        if not values:
            return None
        headers = [str(x or "").strip() for x in values[0]]
        for row in values[1:]:
            if row and str(row[0] or "").strip().upper() == "ИТОГО":
                record = {headers[i]: row[i] if i < len(row) else None for i in range(len(headers))}
                return record
        return None

    def _parse_group_rows(self, values: list[list[Any]]) -> list[dict[str, Any]]:
        if not values:
            return []
        headers=[str(x or '').strip() for x in values[0]]
        out=[]
        for row in values[1:]:
            name=str(row[0] if row else '').strip()
            if not name or name.upper()=='ИТОГО':
                continue
            rec={headers[i]: row[i] if i < len(row) else None for i in range(len(headers))}
            out.append({
                'name': name,
                'margin_pct': self._num(rec.get('Маржинальность')),
                'roi_pct': self._num(rec.get('ROI')),
                'buyout_pct': self._num(rec.get('Процент выкупа')),
                'orders_rub': self._num(rec.get('Сумма заказов')),
                'orders_qty': self._num(rec.get('Кол-во заказов')),
                'buyouts_rub': self._num(rec.get('Сумма выкупов')),
                'buyouts_qty': self._num(rec.get('Кол-во выкупов')),
                'profit_rub': self._num(rec.get('Сумма прибыли')),
                'seller_articles': [x.strip() for x in str(rec.get('Артикулы продавца') or '').split(';') if x.strip()],
            })
        return out

    def _parse_comparison_changes(self, payload: dict[str, Any], out: dict[str, Any]) -> None:
        values = self._range_values(payload, "weekly_summary", "comparison")
        if len(values) < 10:
            return
        block_row = None
        for i, row in enumerate(values[:20]):
            if any(str(x or "").strip().upper() == "ВСЕ МАГАЗИНЫ" for x in row):
                block_row = i
                break
        if block_row is None or block_row + 1 >= len(values):
            return
        names = values[block_row]
        header = values[block_row + 1]
        starts = [i for i, x in enumerate(header) if str(x or "").strip() == "Показатель"]
        if not starts:
            return
        stores = {str(x.get("id")): x for x in out.get("stores", []) if isinstance(x, dict)}
        portfolio = out.setdefault("portfolio", {})
        aliases = {"AIR":"air", "АИР":"air", "САНЫЧ":"sanych", "ХОЗЯЮШКА":"hozyushka"}
        metric_map = {
            "Сумма заказов, ₽": "orders_rub",
            "Сумма выкупов, ₽": "buyouts_rub",
            "Прибыль, ₽": "profit_rub",
            "Маржинальность, %": "margin_pct",
            "Заказы, шт": "orders_qty",
            "Выкупы, шт": "buyouts_qty",
            "Реклама, ₽": "ad_spend_rub",
            "ДРР продаж, %": "drr_pct",
        }
        for start in starts:
            label = str(names[start] if start < len(names) else "").strip().upper()
            target = portfolio if ("ВСЕ" in label or start == starts[0]) else stores.get(aliases.get(label, ""))
            if target is None:
                continue
            for row in values[block_row + 2:]:
                metric = str(row[start] if start < len(row) else "").strip()
                base = metric_map.get(metric)
                if not base:
                    continue
                prev = self._num(row[start + 1] if start + 1 < len(row) else None)
                now = self._num(row[start + 2] if start + 2 < len(row) else None)
                # Standard blocks have delta and delta % in +3/+4. Some Sanych
                # blocks contain duplicated all-store columns in between; calculate
                # the percentage directly when possible instead of trusting layout.
                change = self._pct_change(now, prev)
                if prev is not None:
                    target[f"{base}_prev"] = prev
                if now is not None:
                    target[base] = now
                if change is not None:
                    target[f"{base}_change_pct"] = round(change, 2)

    def _parse_group_changes(self, payload: dict[str, Any], out: dict[str, Any]) -> None:
        values = self._range_values(payload, "weekly_summary", "comparison")
        if not values:
            return
        aliases={"AIR":"air","АИР":"air","САНЫЧ":"sanych","ХОЗЯЮШКА":"hozyushka"}
        stores={str(x.get("id")):x for x in out.get("stores",[]) if isinstance(x,dict)}
        for i,row in enumerate(values):
            first=str(row[0] if row else "").strip().upper()
            if not first.startswith("ПО ТОВАРНЫМ ГРУППАМ"):
                continue
            label=first.split("—",1)[1].strip() if "—" in first else ""
            sid=aliases.get(label)
            store=stores.get(sid or "")
            if not store or i+1>=len(values):
                continue
            groups={str(g.get("name")):g for g in store.get("groups",[]) if isinstance(g,dict)}
            j=i+2
            while j<len(values):
                r=values[j]
                name=str(r[0] if r else "").strip()
                if not name or name.upper().startswith("ПО ТОВАРНЫМ ГРУППАМ"):
                    break
                g=groups.get(name)
                if g:
                    g.update({
                        "buyouts_prev_rub":self._num(r[1] if len(r)>1 else None),
                        "buyouts_now_rub":self._num(r[2] if len(r)>2 else None),
                        "buyouts_change_pct":self._num(r[4] if len(r)>4 else None),
                        "profit_prev_rub":self._num(r[5] if len(r)>5 else None),
                        "profit_now_rub":self._num(r[6] if len(r)>6 else None),
                        "profit_change_pct":self._num(r[8] if len(r)>8 else None),
                        "margin_prev_pct":self._num(r[9] if len(r)>9 else None),
                        "margin_now_pct":self._num(r[10] if len(r)>10 else None),
                        "margin_change_pp":self._num(r[11] if len(r)>11 else None),
                        "orders_prev_rub":self._num(r[12] if len(r)>12 else None),
                        "orders_now_rub":self._num(r[13] if len(r)>13 else None),
                        "orders_change_pct":self._num(r[14] if len(r)>14 else None),
                    })
                j+=1

    def _parse_weekly(self, payload: dict[str, Any], seed: dict[str, Any]) -> dict[str, Any]:
        out = deepcopy(seed)
        stores_by_id = {x.get("id"): x for x in out.get("stores", []) if isinstance(x, dict)}
        mapping = {"air": "air", "sanych": "sanych", "hozyushka": "hozyushka"}
        self._parse_comparison_changes(payload, out)
        for range_key, store_id in mapping.items():
            values=self._range_values(payload, "weekly_summary", range_key)
            rec = self._parse_total_row(values)
            if not rec or store_id not in stores_by_id:
                continue
            store = stores_by_id[store_id]
            fields = {"margin_pct":"Маржинальность","roi_pct":"ROI","orders_rub":"Сумма заказов","orders_qty":"Кол-во заказов","buyouts_rub":"Сумма выкупов","buyouts_qty":"Кол-во выкупов","profit_rub":"Сумма прибыли"}
            for dest, src in fields.items():
                n=self._num(rec.get(src))
                if n is not None: store[dest]=n
            groups=self._parse_group_rows(values)
            if groups: store['groups']=groups
            store["source"] = "google_sheets_bridge:weekly_summary"
        self._parse_group_changes(payload, out)
        return out

    def _parse_own_27(self, payload: dict[str, Any], out: dict[str, Any]) -> None:
        values = self._range_values(payload, "own_27", "summary")
        if values:
            header_idx = None
            for i, row in enumerate(values):
                if row and str(row[0] or "").strip() == "Артикул продавца WB":
                    header_idx = i
                    break
            if header_idx is not None and header_idx > 0:
                headers = [str(x or "").strip() for x in values[header_idx]]
                total = values[header_idx - 1]
                m = {headers[i]: total[i] if i < len(total) else None for i in range(len(headers))}
                own = out.setdefault("own_27", {})
                keys = {
                    "fbs_debt_orders": "FBS долг по заказам",
                    "ff_stock": "Остатки ФФ",
                    "k2_safe_stock": "К2 ФФ · SAFE",
                    "fbw_stock": "Остатки FBW",
                    "in_way_to_client": "в пути к клиенту",
                    "in_way_from_client": "в пути от клиенту",
                    "wb_fbs_stock": "Остатки WB FBS",
                    "ozon_fbs_stock": "Остатки Ozon FBS",
                    "sales_qty": "Продажи, шт",
                    "orders_qty": "Заказы, шт",
                    "orders_rub": "Заказы,руб",
                }
                for dest, src in keys.items():
                    n = self._num(m.get(src))
                    if n is not None:
                        own[dest] = n
                own["source"] = "google_sheets_bridge:own_27"

            # Product-level operational facts from 27. These feed concrete supply decisions.
            if header_idx is not None:
                headers = [str(x or "").strip() for x in values[header_idx]]
                products=[]
                for row in values[header_idx+1:]:
                    rec={headers[i]: row[i] if i < len(row) else None for i in range(len(headers))}
                    sku=str(rec.get("Артикул WB") or "").strip()
                    if not sku.isdigit():
                        continue
                    safe=self._num(rec.get("К2 ФФ · SAFE"))
                    ff=self._num(rec.get("Остатки ФФ"))
                    products.append({
                        "sku":sku,"name":str(rec.get("Артикул продавца WB") or ""),
                        "price_client_rub":self._num(rec.get("Цена для клиента")),
                        "fbs_debt_orders":self._num(rec.get("FBS долг по заказам")),
                        "ff_stock":ff,"k2_safe_stock":safe,
                        "fbw_stock":self._num(rec.get("Остатки FBW")),
                        "wb_fbs_stock":self._num(rec.get("Остатки WB FBS")),
                        "sales_qty":self._num(rec.get("Продажи, шт")),
                        "orders_qty":self._num(rec.get("Заказы, шт")),
                        "orders_per_day":self._num(rec.get("Заказов в день")),
                        "orders_rub":self._num(rec.get("Заказы,руб")),
                    })
                    wb_stock=self._num(rec.get("Остатки WB FBS"))
                    products[-1]["safe_stock"] = safe if safe is not None else (ff if ff is not None else wb_stock)
                    products[-1]["safe_stock_source"] = "K2 SAFE" if safe is not None else ("FF" if ff is not None else ("WB FBS stock" if wb_stock is not None else ""))
                if products:
                    out.setdefault("own_27", {})["products"] = products

        # Daily all-orders history gives the demand controller a real short-vs-baseline
        # signal instead of extrapolating from a single "orders/day" cell. We count
        # marketplace demand here (including later cancellations) intentionally: stock
        # planning and advertising pressure should react to incoming demand, while
        # buyout/quality is handled by its own contour.
        order_rows = self._range_values(payload, "own_27", "orders_history")
        if len(order_rows) >= 2:
            headers = [str(x or "").strip() for x in order_rows[0]]
            idx = {h: i for i, h in enumerate(headers)}
            date_i, sku_i = idx.get("Дата заказа"), idx.get("Артикул WB")
            by_sku: dict[str, dict[date, int]] = {}
            all_dates: list[date] = []
            if date_i is not None and sku_i is not None:
                for row in order_rows[1:]:
                    if max(date_i, sku_i) >= len(row):
                        continue
                    d = self._as_date(row[date_i])
                    sku = str(row[sku_i] or "").strip()
                    if not d or not sku.isdigit():
                        continue
                    by_sku.setdefault(sku, {})[d] = by_sku.setdefault(sku, {}).get(d, 0) + 1
                    all_dates.append(d)
            if all_dates:
                # The newest day may still be in progress. Exclude it from the trend
                # windows so an afternoon refresh does not look like a demand collapse.
                end = max(all_dates) - timedelta(days=1)
                def avg_for(counts: dict[date, int], start: date, finish: date) -> float:
                    days = (finish - start).days + 1
                    if days <= 0:
                        return 0.0
                    return sum(counts.get(start + timedelta(days=i), 0) for i in range(days)) / days
                own = out.setdefault("own_27", {})
                by_product = {str(x.get("sku")): x for x in own.get("products", []) if x.get("sku")}
                for sku, counts in by_sku.items():
                    prod = by_product.get(sku)
                    if not prod:
                        continue
                    short_start = end - timedelta(days=2)
                    baseline_end = short_start - timedelta(days=1)
                    baseline_start = baseline_end - timedelta(days=10)
                    last7_start = end - timedelta(days=6)
                    prior21_end = last7_start - timedelta(days=1)
                    prior21_start = prior21_end - timedelta(days=20)
                    short_avg = avg_for(counts, short_start, end)
                    base_avg = avg_for(counts, baseline_start, baseline_end)
                    avg7 = avg_for(counts, last7_start, end)
                    avg21 = avg_for(counts, prior21_start, prior21_end)
                    prod["orders_daily_3d"] = round(short_avg, 3)
                    prod["orders_daily_baseline_11d"] = round(base_avg, 3)
                    prod["orders_trend_pct"] = round(self._pct_change(short_avg, base_avg), 2) if self._pct_change(short_avg, base_avg) is not None else None
                    prod["orders_daily_7d"] = round(avg7, 3)
                    prod["orders_daily_prior_21d"] = round(avg21, 3)
                    prod["orders_trend_7v21_pct"] = round(self._pct_change(avg7, avg21), 2) if self._pct_change(avg7, avg21) is not None else None
                    history_start = end - timedelta(days=59)
                    prod["orders_daily_history"] = [
                        {"date": (history_start + timedelta(days=i)).isoformat(), "orders": counts.get(history_start + timedelta(days=i), 0)}
                        for i in range(60)
                    ]
                    prod["demand_history_asof"] = end.isoformat()

        # Supply-plan hints from the current operating table.  "Already ordered" is
        # deliberately NOT treated as available stock because the arrival date/receipt
        # is not proven here.  It is stored as planned incoming and shown to the decision
        # engine as a separate, lower-confidence fact.
        supply_rows = self._range_values(payload, "own_27", "supply_plan")
        if len(supply_rows) >= 2:
            headers = [str(x or "").strip() for x in supply_rows[0]]
            idx = {h:i for i,h in enumerate(headers)}
            sku_i = idx.get("WB nmId")
            if sku_i is not None:
                own = out.setdefault("own_27", {})
                by_product = {str(x.get("sku")): x for x in own.get("products", []) if x.get("sku")}
                for row in supply_rows[1:]:
                    if sku_i >= len(row):
                        continue
                    sku = str(row[sku_i] or "").strip()
                    prod = by_product.get(sku)
                    if not prod:
                        continue
                    rec = {headers[i]: row[i] if i < len(row) else None for i in range(len(headers))}
                    incoming = self._num(rec.get("Уже заказано"))
                    need = self._num(rec.get("Потребность"))
                    target = self._num(rec.get("Цель дней"))
                    reorder = self._num(rec.get("Точка заказа"))
                    if incoming is not None:
                        prod["planned_incoming_qty"] = max(0.0, incoming)
                        prod["planned_incoming_confirmed"] = False
                    if need is not None:
                        prod["supply_need_qty"] = max(0.0, need)
                    if target is not None:
                        prod["supply_target_days"] = target
                    if reorder is not None:
                        prod["reorder_point_days"] = reorder

        # Current order lifecycle: created -> buyout/cancel with real lag by SRID.
        # This prevents a fresh order surge from being compared directly with buyouts
        # and profit that belong to older cohorts.
        lifecycle_rows = self._range_values(payload, "own_27", "order_lifecycle")
        if len(lifecycle_rows) >= 2:
            cohort = analyze_order_lifecycle(lifecycle_rows)
            own = out.setdefault("own_27", {})
            own["cohort_lifecycle"] = cohort
            by_product = {str(x.get("sku")): x for x in own.get("products", []) if x.get("sku")}
            for sku, summary in (cohort.get("by_sku") or {}).items():
                prod = by_product.get(str(sku))
                if not prod or not isinstance(summary, dict):
                    continue
                prod["cohort"] = summary
                b = summary.get("buyout_lag") or {}
                r7 = summary.get("recent_7d") or {}
                prod["buyout_lag_p50_days"] = b.get("p50_days")
                prod["buyout_lag_p90_days"] = b.get("p90_days")
                prod["recent_cohort_maturity_pct"] = r7.get("maturity_pct")
                prod["recent_cohort_quality_ready"] = bool(r7.get("quality_ready"))
                prod["recent_cohort_orders_7d"] = r7.get("orders")
                prod["recent_cohort_open_7d"] = r7.get("open_orders")
                prod["recent_cohort_buyouts_observed_7d"] = r7.get("buyouts_observed")
                prod["recent_cohort_cancels_observed_7d"] = r7.get("cancels_observed")
                prod["unresolved_older_than_buyout_p90"] = summary.get("unresolved_older_than_p90")

        unit = self._range_values(payload, "own_27", "unit_economics")
        if len(unit) >= 2:
            headers = [str(x or "").strip() for x in unit[1]]
            examples = []
            for row in unit[2:]:
                if len(row) < 24 or not str(row[2] if len(row) > 2 else "").strip().isdigit():
                    continue
                rec = {headers[i]: row[i] if i < len(row) else None for i in range(len(headers))}
                margin = self._num(rec.get("Маржинальность, %"))
                profit = self._num(rec.get("Прибыль, руб"))
                examples.append({
                    "sku": str(rec.get("Артикул WB") or ""),
                    "name": str(rec.get("Артикул продавца WB") or ""),
                    "cost_rub": self._num(rec.get("Себестоимость, руб")),
                    "price_rub": self._num(rec.get("Цена WB, руб")),
                    "price_client_rub": self._num(rec.get("Цена клиента, руб")),
                    "profit_rub": profit,
                    "margin_pct": margin,
                    "roi_pct": self._num(rec.get("ROI, %")),
                    "drr_pct": self._num(rec.get("ДРР по заказам Sellmon, %")),
                    "historical_buyout_pct": self._num(rec.get("Исторический выкуп Sellmon, %")),
                    "commission_pct": self._num(rec.get("Комиссия FBS новая, %")),
                    "logistics_total_rub": self._num(rec.get("Логистика итого, руб")),
                    "acceptance_rub": self._num(rec.get("Платная приемка, руб")),
                    "constructor_pct": self._num(rec.get("Конструктор тарифов, %")),
                    "acquiring_pct": self._num(rec.get("Эквайринг, %")),
                    "tax_total_rub": self._num(rec.get("Налоги всего, руб")),
                    "target_profit_rub": self._num(rec.get("План прибыль, руб")),
                    "target_margin_pct": self._num(rec.get("План маржинальность, %")),
                    "target_roi_pct": self._num(rec.get("План ROI, %")),
                    "target_price_profit_rub": self._num(rec.get("Цена WB под план прибыль")),
                    "target_price_margin_rub": self._num(rec.get("Цена WB под план маржу")),
                    "target_price_roi_rub": self._num(rec.get("Цена WB под план ROI")),
                    "unit_status": str(rec.get("Статус") or ""),
                    "cabinet": str(rec.get("Кабинет") or ""),
                })
            if examples:
                own=out.setdefault("own_27", {})
                own["economy_examples"] = examples
                econ={str(x.get("sku")):x for x in examples if x.get("sku")}
                for prod in own.get("products", []):
                    e=econ.get(str(prod.get("sku")))
                    if e: prod.update(e)

        ff = self._range_values(payload, "own_27", "ff_history")
        if len(ff) >= 2:
            headers = [str(x or "").strip() for x in ff[0]]
            idx = {h: i for i, h in enumerate(headers)}
            rows = []
            latest = None
            for row in ff[1:]:
                date = str(row[idx.get("Дата", 0)] if len(row) > idx.get("Дата", 0) else "")
                if date and (latest is None or date > latest):
                    latest = date
                available_i = idx.get("Доступно")
                if available_i is None or available_i >= len(row):
                    continue
                available = self._num(row[available_i])
                if available is None:
                    continue
                name_i, sku_i = idx.get("Название"), idx.get("SKU / учётный артикул")
                rows.append({
                    "sku": str(row[sku_i] if sku_i is not None and sku_i < len(row) else ""),
                    "name": str(row[name_i] if name_i is not None and name_i < len(row) else ""),
                    "available": available,
                })
            if latest:
                out.setdefault("own_27", {})["ff_snapshot_date"] = latest
            if rows:
                out.setdefault("own_27", {})["ff_examples"] = sorted(rows, key=lambda x: x["available"])[:30]

    def _link_products_to_weekly_groups(self, out: dict[str, Any]) -> None:
        """Attach current realised group economics to SKU facts.

        Weekly buyouts/profit are lagging outcomes, so this is a guard/context layer,
        not a same-cohort attribution. Exact seller article membership from the weekly
        summary is used instead of fuzzy name matching whenever available.
        """
        products = [x for x in out.get("own_27", {}).get("products", []) if isinstance(x, dict)]
        if not products:
            return
        by_article: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        for store in out.get("stores", []) or []:
            if not isinstance(store, dict):
                continue
            for group in store.get("groups", []) or []:
                if not isinstance(group, dict):
                    continue
                for art in group.get("seller_articles", []) or []:
                    key = str(art).strip().casefold()
                    if key:
                        by_article.setdefault(key, []).append((store, group))
        for prod in products:
            key = str(prod.get("name") or "").strip().casefold()
            matches = by_article.get(key, [])
            if not matches:
                continue
            # Prefer the store that matches the cabinet label from Unitka.
            cabinet = str(prod.get("cabinet") or "").casefold()
            store, group = matches[0]
            for s, g in matches:
                sname = str(s.get("name") or "").casefold()
                sid = str(s.get("id") or "").casefold()
                if cabinet and (sname in cabinet or sid in cabinet or ("саныч" in cabinet and "саныч" in sname) or ("аир" in cabinet and ("air" in sid or "аир" in sname)) or ("хозяюш" in cabinet and "хозяюш" in sname)):
                    store, group = s, g
                    break
            prod.update({
                "weekly_store_id": store.get("id"),
                "weekly_store_name": store.get("name"),
                "weekly_group": group.get("name"),
                "group_margin_pct": group.get("margin_pct"),
                "group_profit_rub": group.get("profit_rub"),
                "group_buyout_pct": group.get("buyout_pct"),
                "group_orders_rub": group.get("orders_rub"),
                "group_buyouts_rub": group.get("buyouts_rub"),
                "group_orders_change_pct": group.get("orders_change_pct"),
                "group_buyouts_change_pct": group.get("buyouts_change_pct"),
                "group_profit_change_pct": group.get("profit_change_pct"),
                "group_margin_change_pp": group.get("margin_change_pp"),
                "group_economics_note": "Недельный выкуп/прибыль — более зрелый итог периода, не та же когорта, что текущие заказы.",
            })

    def _parse_search_signals(self, payload: dict[str, Any], out: dict[str, Any]) -> None:
        values = self._range_values(payload, "sanych_sellmonitor", "positions")
        if not values:
            return
        header_idx = None
        for i, row in enumerate(values[:10]):
            labels = {str(x or "").strip() for x in row}
            if {"nmId", "Частотность", "Текущая позиция"}.issubset(labels):
                header_idx = i
                break
        if header_idx is None:
            return
        headers = [str(x or "").strip() for x in values[header_idx]]
        idx = {h: i for i, h in enumerate(headers)}
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in values[header_idx + 1:]:
            try:
                active = str(row[idx["Активен"]] if idx.get("Активен") is not None and idx["Активен"] < len(row) else "TRUE").upper()
                sku = str(row[idx["nmId"]] if idx["nmId"] < len(row) else "").strip()
                if active not in {"TRUE", "1", "ДА"} or not sku.isdigit():
                    continue
                freq = self._num(row[idx["Частотность"]] if idx["Частотность"] < len(row) else None)
                pos = self._num(row[idx["Текущая позиция"]] if idx["Текущая позиция"] < len(row) else None)
                query = str(row[idx["Поисковый запрос"]] if idx["Поисковый запрос"] < len(row) else "")
                snap = str(row[idx["Дата снимка"]] if idx.get("Дата снимка") is not None and idx["Дата снимка"] < len(row) else "")
                target = self._num(row[idx["Эффективная цель"]] if idx.get("Эффективная цель") is not None and idx["Эффективная цель"] < len(row) else None)
                grouped.setdefault(sku, []).append({"query": query, "frequency": freq or 0.0, "position": pos, "target": target, "snapshot": snap})
            except Exception:
                continue
        products = {str(x.get("sku")): x for x in out.get("own_27", {}).get("products", []) if x.get("sku")}
        previous = self._read_json(self.live_path) if self.live_path.exists() else None
        previous_products = {str(x.get("sku")): x for x in (previous or {}).get("own_27", {}).get("products", []) if x.get("sku")}
        for sku, rows in grouped.items():
            prod = products.get(sku)
            if not prod:
                continue
            # Total query demand is useful for trend detection; the top-frequency query
            # is kept for human explanation. Duplicate phrases are counted only once.
            unique: dict[str, dict[str, Any]] = {}
            for r in rows:
                q = str(r.get("query") or "").strip().lower()
                if not q:
                    continue
                if q not in unique or (r.get("snapshot") or "") > (unique[q].get("snapshot") or ""):
                    unique[q] = r
            ranked = sorted(unique.values(), key=lambda r: float(r.get("frequency") or 0), reverse=True)
            total_freq = sum(float(r.get("frequency") or 0) for r in ranked)
            prod["search_frequency_current"] = round(total_freq, 2)
            if ranked:
                prod["top_search_query"] = ranked[0].get("query")
                prod["top_search_frequency"] = ranked[0].get("frequency")
                prod["top_search_position"] = ranked[0].get("position")
                prod["top_search_target_position"] = ranked[0].get("target")
                prod["search_snapshot_at"] = ranked[0].get("snapshot")
            prev = previous_products.get(sku) or {}
            prev_freq = self._num(prev.get("search_frequency_current"))
            if prev_freq and prev_freq > 0:
                prod["search_frequency_trend_pct"] = round((total_freq / prev_freq - 1.0) * 100.0, 2)
            prev_pos = self._num(prev.get("top_search_position"))
            cur_pos = self._num(prod.get("top_search_position"))
            if prev_pos is not None and cur_pos is not None:
                prod["search_position_delta"] = round(cur_pos - prev_pos, 2)

    def _merge_bridge(self, payload: dict[str, Any]) -> dict[str, Any]:
        seed = self._read_json(self.seed_path) or {}
        out = self._parse_weekly(payload, seed)
        self._parse_own_27(payload, out)
        self._link_products_to_weekly_groups(out)
        self._parse_search_signals(payload, out)
        # Refresh source-health timestamps from the bridge without allowing a stale source
        # to silently become authoritative. Trust/usage policy remains local.
        bridge_sources = payload.get("sources") or {}
        for h in out.get("source_health", []):
            src = bridge_sources.get(h.get("id")) if isinstance(h, dict) else None
            if src and src.get("modified_at"):
                h["bridge_modified_at"] = src.get("modified_at")
                h["reachable"] = True
        out["generated_at"] = payload.get("generated_at") or datetime.now(timezone.utc).isoformat()
        out["mode"] = "live_google_sheets"
        out["bridge"] = {"ok": True, "sources_received": sorted((payload.get("sources") or {}).keys())}
        return out

    def merge_payload(self, payload: dict[str, Any], origin: str = "auto_import") -> dict[str, Any]:
        merged=self._merge_bridge(payload)
        merged["mode"] = origin
        self.live_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        return merged

    def refresh(self) -> dict[str, Any]:
        url = (self.settings.google_sheets_bridge_url or "").strip()
        key = (self.settings.google_sheets_bridge_key or "").strip()
        if not url or not key:
            raise RuntimeError("Google Sheets bridge is not configured")
        request = {"key": key, "action": "all"}
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.post(url, json=request)
            resp.raise_for_status()
            payload = resp.json()
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("error") or "Sheets bridge returned error"))
        merged = self._merge_bridge(payload)
        self.live_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        return merged
