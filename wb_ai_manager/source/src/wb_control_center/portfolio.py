from __future__ import annotations

import json
import re
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
            live["current_data"] = True
            live["data_status"] = "current"
            live["historical_only"] = False
            return live
        seed = self._read_json(self.seed_path) or {}
        seed = deepcopy(seed)
        seed["data_origin"] = "seeded_real_facts"
        seed["current_data"] = False
        seed["data_status"] = "historical_snapshot"
        seed["historical_only"] = True
        seed["stale_reason"] = "Живой источник портфеля не подключён. Показан последний сохранённый исторический срез; он не используется для текущих денежных решений."
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
                "mode": "apps_script_bridge" if (self.settings.google_sheets_bridge_url and self.settings.google_sheets_bridge_key) else ("saved_live_snapshot" if self.live_path.exists() else "not_connected"),
                "bridge_url_set": bool(self.settings.google_sheets_bridge_url),
                "bridge_key_set": bool(self.settings.google_sheets_bridge_key),
                "last_live_snapshot": self._read_json(self.live_path).get("generated_at") if self.live_path.exists() and self._read_json(self.live_path) else None,
            },
            "sources": sorted(sources, key=lambda x: (-int(x.get("priority") or 0), x["title"])),
        }

    @staticmethod
    def _category_name(value: Any) -> str:
        text = str(value or "").strip()
        if "·" in text and "ozon" in text.lower():
            text = text.split("·", 1)[0].strip()
        return text or "Без категории"

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

    @staticmethod
    def _economics_norm(value: Any) -> str:
        text = str(value or "").strip().lower().replace("ё", "е").replace("×", "x").replace("х", "x")
        # Parenthetical manufacturing detail such as "(2×5 кг)" must not stop a
        # seller article "цемент 10 кг" from matching the 10 kg plan row.
        text = re.sub(r"\([^)]*\)", " ", text)
        text = re.sub(r"(\d+(?:[.,]\d+)?)\s*(л|кг|м)\b", lambda m: m.group(1).replace(",", ".") + m.group(2), text)
        text = re.sub(r"(?:x\s*)?(\d+)\s*шт\b", lambda m: ("" if m.group(1) == "1" else " pack" + m.group(1)), text)
        text = re.sub(r"\bx\s*(\d+)\b", lambda m: ("" if m.group(1) == "1" else " pack" + m.group(1)), text)
        replacements = {
            "пластиковое": "пласт", "пластиковый": "пласт", "пластиковая": "пласт",
            "бежевое": "беж", "бежевый": "беж", "серое": "сер", "серый": "сер",
        }
        for src, dst in replacements.items():
            text = text.replace(src, dst)
        text = re.sub(r"[^a-zа-я0-9.,]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _economics_units(value: Any) -> set[str]:
        text = str(value or "").lower().replace(",", ".")
        return {
            f"{m.group(1)}{m.group(2)}"
            for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(кг|л)\b", text)
        }

    @staticmethod
    def _economics_pack_count(value: Any) -> int | None:
        text = str(value or "").lower().replace("×", "x").replace("х", "x")
        matches = list(re.finditer(r"(\d+)\s*шт\b", text))
        if matches:
            return int(matches[-1].group(1))
        matches = list(re.finditer(r"\bx\s*(\d+)\b", text))
        if matches:
            return int(matches[-1].group(1))
        return None

    def _parse_primary_economics_axis(self, payload: dict[str, Any], out: dict[str, Any]) -> None:
        """Overlay the authoritative plan model and an independent factual snapshot.

        The live Sheets bridge already reads sanych_sellmonitor/06_Остатки.  A compact
        JSON block beginning with an economics_axis_snapshot marker is stored in the
        unused technical tail of that range.  This lets production consume the new
        primary unit-economics model without pretending that plan values are WB facts.
        """
        values = self._range_values(payload, "sanych_sellmonitor", "stocks")
        if not values:
            return
        marker: dict[str, Any] | None = None
        plans: list[dict[str, Any]] = []
        facts: dict[str, dict[str, Any]] = {}
        for row in values:
            for cell in row:
                text = str(cell or "").strip()
                if not text.startswith("{"):
                    continue
                try:
                    record = json.loads(text)
                except Exception:
                    continue
                if not isinstance(record, dict):
                    continue
                kind = str(record.get("type") or "")
                if kind in {"economics_axis_snapshot", "primary_unit_snapshot"}:
                    marker = record
                elif kind == "primary_unit_plan" and record.get("name"):
                    plans.append(record)
                elif kind == "actual_economics_fact":
                    sku = str(record.get("nmId") or "").strip()
                    if sku.isdigit():
                        facts[sku] = record
        if not plans:
            return

        own = out.setdefault("own_27", {})
        products = [x for x in own.get("products", []) if isinstance(x, dict)]
        plan_by_name: dict[str, list[dict[str, Any]]] = {}
        for plan in plans:
            plan_by_name.setdefault(self._economics_norm(plan.get("name")), []).append(plan)

        # Explicit aliases are deliberately small and evidence-backed.  Ambiguous
        # fuzzy matches are never allowed to create a money decision.
        explicit_plan_by_sku = {
            "1442769822": "Ведро-туалет пластиковое бежевое",
            "1515475863": "Ведро-туалет пластиковое серое",
            "1260779020": "Метла уличная (гардена)",
            "1588037156": "Метла уличная + черенок",
        }
        economics_keys = (
            "cost_rub", "price_rub", "price_client_rub", "profit_rub", "margin_pct",
            "roi_pct", "drr_pct", "commission_pct", "logistics_total_rub",
            "acceptance_rub", "constructor_pct", "acquiring_pct", "tax_total_rub",
            "target_profit_rub", "target_margin_pct", "target_roi_pct",
            "target_price_profit_rub", "target_price_margin_rub", "target_price_roi_rub",
        )
        mapped = 0
        unmatched: list[dict[str, Any]] = []

        for prod in products:
            sku = str(prod.get("sku") or "")
            legacy = {k: prod.get(k) for k in economics_keys if prod.get(k) is not None}
            if legacy:
                prod["legacy_unit_economics"] = legacy
            prod["unit_economics_role"] = "legacy_fallback_diagnostic"
            prod["unit_economics_decision_ready"] = False
            prod["unit_economics_source"] = "27/Юнитка — только fallback"

            plan: dict[str, Any] | None = None
            alias = explicit_plan_by_sku.get(sku)
            if alias:
                candidates = plan_by_name.get(self._economics_norm(alias)) or []
                if len(candidates) == 1:
                    plan = candidates[0]
            if plan is None:
                # Seller article wins over the physical/K2 parent row.  This prevents,
                # for example, a 10 kg cement SKU from inheriting the 5 kg plan merely
                # because several variants sit under the same physical source row.
                seller_matches = plan_by_name.get(self._economics_norm(prod.get("name"))) or []
                seller_unique = {int(x.get("source_row") or 0): x for x in seller_matches}
                if len(seller_unique) == 1:
                    plan = next(iter(seller_unique.values()))
            if plan is None and prod.get("physical_position"):
                physical_matches = plan_by_name.get(self._economics_norm(prod.get("physical_position"))) or []
                physical_unique = {int(x.get("source_row") or 0): x for x in physical_matches}
                if len(physical_unique) == 1:
                    candidate = next(iter(physical_unique.values()))
                    seller_units = self._economics_units(prod.get("name"))
                    plan_units = self._economics_units(candidate.get("name"))
                    seller_pack = self._economics_pack_count(prod.get("name"))
                    plan_pack = self._economics_pack_count(candidate.get("name"))
                    units_ok = not seller_units or not plan_units or seller_units == plan_units
                    pack_ok = (
                        seller_pack is None
                        or seller_pack == 1 and plan_pack in {None, 1}
                        or seller_pack is not None and seller_pack > 1 and seller_pack == plan_pack
                    )
                    if units_ok and pack_ok:
                        plan = candidate

            if plan is None:
                unmatched.append({"sku": sku, "name": prod.get("name"), "physical_position": prod.get("physical_position")})
            else:
                mapped += 1
                prod.update({
                    "cost_rub": self._num(plan.get("cost_rub")),
                    "price_rub": self._num(plan.get("price_after_discount_rub")),
                    "price_client_rub": self._num(plan.get("price_after_spp_rub")),
                    "profit_rub": self._num(plan.get("plan_profit_rub")),
                    "margin_pct": self._num(plan.get("plan_margin_pct")),
                    "roi_pct": self._num(plan.get("plan_roi_pct")),
                    "drr_pct": self._num(plan.get("plan_drr_pct")),
                    "plan_drr_pct": self._num(plan.get("plan_drr_pct")),
                    "commission_pct": self._num(plan.get("commission_pct")),
                    "logistics_total_rub": self._num(plan.get("logistics_total_rub")),
                    "acceptance_rub": self._num(plan.get("acceptance_rub")),
                    "constructor_pct": self._num(plan.get("constructor_pct")),
                    "acquiring_pct": self._num(plan.get("acquiring_pct")),
                    "tax_total_rub": self._num(plan.get("tax_total_rub")),
                    "buyout_plan_pct": self._num(plan.get("buyout_plan_pct")),
                    "price_before_discount_rub": self._num(plan.get("price_before_discount_rub")),
                    "discount_pct": self._num(plan.get("discount_pct")),
                    "spp_pct": self._num(plan.get("spp_pct")),
                    "logistics_base_rub": self._num(plan.get("logistics_base_rub")),
                    "acquiring_rub": self._num(plan.get("acquiring_rub")),
                    "tax_regime": plan.get("tax_regime"),
                    "target_profit_rub": self._num(plan.get("target_profit_rub")),
                    "target_margin_pct": self._num(plan.get("target_margin_pct")),
                    "target_roi_pct": self._num(plan.get("target_roi_pct")),
                    "target_price_profit_rub": self._num(plan.get("target_price_profit_rub")),
                    "target_price_margin_rub": self._num(plan.get("target_price_margin_rub")),
                    "target_price_roi_rub": self._num(plan.get("target_price_roi_rub")),
                    "primary_unit_plan_name": plan.get("name"),
                    "primary_unit_group": plan.get("group"),
                    "primary_unit_source_row": plan.get("source_row"),
                    "unit_economics_source": "Юнит-экономика вб / WB FBS новая",
                    "unit_economics_role": "primary_plan",
                    "unit_economics_decision_ready": True,
                })

            fact = facts.get(sku)
            if fact:
                prod.update({
                    "fact_ad_spend_rub": self._num(fact.get("ad_spend_rub")),
                    "fact_sales_revenue_rub": self._num(fact.get("sales_revenue_rub")),
                    "fact_sales_qty": self._num(fact.get("sales_qty")),
                    "fact_drr_sales_pct": self._num(fact.get("fact_drr_sales_pct")),
                    "fact_economics_period_from": fact.get("from_date"),
                    "fact_economics_period_to": fact.get("to_date"),
                    "fact_economics_days": self._num(fact.get("days")),
                    "fact_economics_quality": fact.get("quality"),
                    "fact_economics_source": fact.get("source"),
                })

        own["primary_unit_economics"] = {
            "source": "Юнит-экономика вб / WB FBS новая",
            "source_spreadsheet_id": (marker or {}).get("primary_spreadsheet_id") or (marker or {}).get("source_spreadsheet_id"),
            "snapshot_date": (marker or {}).get("snapshot_date"),
            "plan_records": len(plans),
            "fact_records": len(facts),
            "mapped_products": mapped,
            "unmatched_products": len(unmatched),
            "fact_period": (marker or {}).get("fact_period"),
            "semantics": "plan_model_plus_independent_fact",
        }
        own["primary_unit_unmatched_examples"] = unmatched[:30]

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
                    "fbs_debt_orders": ("FBS долг по заказам",),
                    "ff_stock": ("Остатки ФФ",),
                    "k2_safe_stock": ("К2 ФФ · SAFE", "К2 ФФ"),
                    "fbw_stock": ("Остатки FBW",),
                    "in_way_to_client": ("в пути к клиенту",),
                    "in_way_from_client": ("в пути от клиенту",),
                    "wb_fbs_stock": ("Остатки WB FBS",),
                    "ozon_fbs_stock": ("Остатки Ozon FBS",),
                    "sales_qty": ("Продажи, шт",),
                    "orders_qty": ("Заказы, шт",),
                    "orders_rub": ("Заказы,руб",),
                }
                for dest, aliases in keys.items():
                    n = None
                    for src in aliases:
                        n = self._num(m.get(src))
                        if n is not None:
                            break
                    if n is not None:
                        own[dest] = n
                own["source"] = "google_sheets_bridge:own_27"

            # Product-level operational facts from Сводная. The sheet contains
            # aggregate physical-product rows (for example "Бидон 5л") followed
            # by marketplace variants. Keep both levels: the aggregate row is the
            # human product group, while WB/Ozon IDs identify concrete listings.
            if header_idx is not None:
                headers = [str(x or "").strip() for x in values[header_idx]]
                header_positions: dict[str, list[int]] = {}
                for col_idx, header in enumerate(headers):
                    header_positions.setdefault(header, []).append(col_idx)

                def row_cell(row: list[Any], header: str, occurrence: int = 0) -> Any:
                    positions = header_positions.get(header) or []
                    if occurrence >= len(positions):
                        return None
                    col = positions[occurrence]
                    return row[col] if col < len(row) else None

                products=[]
                ozon_products=[]
                groups: dict[str, dict[str, Any]] = {}
                current_physical_position = ""
                for sheet_row, row in enumerate(values[header_idx+1:], start=header_idx + 2):
                    rec={headers[i]: row[i] if i < len(row) else None for i in range(len(headers))}
                    row_name=str(rec.get("Артикул продавца WB") or "").strip()
                    sku=str(rec.get("Артикул WB") or "").strip()
                    ozon_sku=str(rec.get("Ozon артикул") or "").strip()
                    ozon_article=str(rec.get("Артикул продавца Ozon") or "").strip()
                    subject=str(rec.get("Предмет WB") or "").strip()

                    if not sku.isdigit() and not ozon_sku.isdigit():
                        # Preserve the preceding physical/K2 row as identity context.
                        # It is useful for deterministic reconciliation with the primary
                        # unit-economics product names, but it is not itself a listing.
                        if row_name:
                            current_physical_position = row_name
                        continue

                    category = self._category_name(subject)

                    k2=self._num(rec.get("К2 ФФ · SAFE"))
                    if k2 is None:
                        k2=self._num(rec.get("К2 ФФ"))
                    ff=self._num(rec.get("Остатки ФФ"))
                    ivanovo=self._num(rec.get("ФФ Иваново"))
                    wb_stock=self._num(rec.get("Остатки WB FBS"))
                    ozon_stock=self._num(rec.get("Остатки Ozon FBS"))

                    if sku.isdigit():
                        # For marketplace availability WB FBS is authoritative.
                        # K2/FF are physical fulfilment pools and are kept separately.
                        market_stock = wb_stock
                        supply_stock = k2 if k2 is not None else (ff if ff is not None else ivanovo)
                        safe_stock = market_stock if market_stock is not None else supply_stock
                        safe_source = "WB FBS" if market_stock is not None else (
                            "K2" if k2 is not None else ("FF" if ff is not None else ("FF Иваново" if ivanovo is not None else ""))
                        )
                        prod={
                            "sku":sku,
                            "name":row_name,
                            "group_name":category,
                            "category_name":category,
                            "sheet_row":sheet_row,
                            "subject":subject,
                            "physical_position": current_physical_position or None,
                            "price_client_rub":self._num(rec.get("Цена для клиента")),
                            "fbs_debt_orders":self._num(rec.get("FBS долг по заказам")),
                            "ff_stock":ff,
                            "k2_safe_stock":k2,
                            "ivanovo_stock":ivanovo,
                            "fbw_stock":self._num(rec.get("Остатки FBW")),
                            "wb_fbs_stock":wb_stock,
                            "ozon_fbs_stock":ozon_stock,
                            "ozon_seller_article":ozon_article or None,
                            "ozon_sku":ozon_sku if ozon_sku.isdigit() else None,
                            "sales_qty":self._num(rec.get("Продажи, шт")),
                            "orders_qty":self._num(rec.get("Заказы, шт")),
                            # "Сводная" contains two identically named columns:
                            # the first "Заказов в день" is WB, the second is Ozon.
                            # A dict keyed by header silently overwrote WB with Ozon.
                            "orders_per_day":self._num(row_cell(row, "Заказов в день", 0)),
                            "ozon_orders_per_day":self._num(row_cell(row, "Заказов в день", 1)),
                            "orders_rub":self._num(rec.get("Заказы,руб")),
                            "safe_stock":safe_stock,
                            "safe_stock_source":safe_source,
                        }
                        products.append(prod)
                        g=groups.setdefault(category, {"name":category,"wb_nm_ids":[],"ozon_skus":[],"variants":[]})
                        g["wb_nm_ids"].append(sku)
                        g["variants"].append({"platform":"WB","id":sku,"article":row_name})

                    if ozon_sku.isdigit():
                        cabinet = "Ozon каб.2" if ("Ozon каб.2" in subject or "Ozon каб.2" in row_name) else "Ozon каб.1"
                        oz={
                            "sku":ozon_sku,
                            "seller_article":ozon_article,
                            "name":row_name or ozon_article,
                            "group_name":category,
                            "category_name":category,
                            "cabinet":cabinet,
                            "stock":ozon_stock,
                            "sheet_row":sheet_row,
                        }
                        ozon_products.append(oz)
                        g=groups.setdefault(category, {"name":category,"wb_nm_ids":[],"ozon_skus":[],"variants":[]})
                        g["ozon_skus"].append(ozon_sku)
                        g["variants"].append({"platform":cabinet,"id":ozon_sku,"article":ozon_article or row_name})

                own = out.setdefault("own_27", {})
                if products:
                    own["products"] = products
                if ozon_products:
                    own["ozon_products"] = ozon_products
                if groups:
                    own["product_groups"] = list(groups.values())
                own["identity_source"] = "Сводная"

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

    def _parse_ozon_operating(self, payload: dict[str, Any], out: dict[str, Any]) -> None:
        own = out.setdefault("own_27", {})
        cabinet_values = self._range_values(payload, "own_27", "ozon_cabinets")
        cabinet_statuses: list[dict[str, Any]] = []
        feed_by_key: dict[tuple[str, str], dict[str, Any]] = {}

        # Top status block: Кабинет / Название / Статус API / Карточек / FBS...
        if len(cabinet_values) >= 2:
            header = [str(x or "").strip() for x in cabinet_values[1]]
            idx = {h: i for i, h in enumerate(header)}
            cab_i = idx.get("Кабинет")
            if cab_i is not None:
                for row in cabinet_values[2:8]:
                    if cab_i >= len(row):
                        continue
                    cab = str(row[cab_i] or "").strip()
                    if not cab.isdigit():
                        continue
                    def cell(name: str):
                        i = idx.get(name)
                        return row[i] if i is not None and i < len(row) else None
                    cabinet_statuses.append({
                        "cabinet": cab,
                        "name": str(cell("Название") or "").strip(),
                        "api_status": str(cell("Статус API") or "").strip(),
                        "cards": self._num(cell("Карточек")),
                        "fbs_positions": self._num(cell("FBS позиций")),
                        "stock": self._num(cell("На складе")),
                        "reserve": self._num(cell("Резерв")),
                        "available": self._num(cell("Доступно")),
                        "comment": str(cell("Комментарий") or "").strip(),
                    })

        # Product lists for cabinet 1/2 live in parallel blocks in "Ozon кабинеты".
        current_cab: str | None = None
        current_col: int | None = None
        for row in cabinet_values:
            marker_found = False
            for i, value in enumerate(row):
                marker = str(value or "").strip().upper()
                if marker in {"КАБИНЕТ 1", "КАБИНЕТ 2"}:
                    current_cab = marker.rsplit(" ", 1)[-1]
                    current_col = i
                    marker_found = True
                    break
            if marker_found or current_cab is None or current_col is None:
                continue
            if current_col >= len(row):
                continue
            article = str(row[current_col] or "").strip()
            if not article or article == "Артикул" or article.upper().startswith("КАБИНЕТ "):
                continue
            name = str(row[current_col + 1] if current_col + 1 < len(row) else "").strip()
            stock = self._num(row[current_col + 2] if current_col + 2 < len(row) else None)
            reserve = self._num(row[current_col + 3] if current_col + 3 < len(row) else None)
            available = self._num(row[current_col + 4] if current_col + 4 < len(row) else None)
            # Avoid accidental section labels; a real product row has a name or stock facts.
            if not name and stock is None and available is None:
                continue
            feed_by_key[(current_cab, article)] = {
                "cabinet": current_cab,
                "seller_article": article,
                "name": name,
                "stock": stock,
                "reserve": reserve,
                "available": available,
            }

        order_values = self._range_values(payload, "own_27", "ozon_orders")
        orders_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        if len(order_values) >= 2:
            headers = [str(x or "").strip() for x in order_values[0]]
            idx = {h: i for i, h in enumerate(headers)}
            for row in order_values[1:]:
                def val(name: str):
                    i = idx.get(name)
                    return row[i] if i is not None and i < len(row) else None
                cab = str(val("Кабинет") or "").strip()
                article = str(val("Артикул") or "").strip()
                if not cab or not article:
                    continue
                qty = self._num(val("Кол-во")) or 0.0
                amount = self._num(val("Сумма строки")) or 0.0
                status = str(val("Статус") or "").strip().lower()
                accepted = str(val("Принят в обработку") or "").strip()
                key = (cab, article)
                agg = orders_by_key.setdefault(key, {
                    "cabinet": cab,
                    "seller_article": article,
                    "orders_qty": 0.0,
                    "cancelled_qty": 0.0,
                    "orders_rub": 0.0,
                    "last_order_at": None,
                })
                if status == "cancelled":
                    agg["cancelled_qty"] += qty
                else:
                    agg["orders_qty"] += qty
                    agg["orders_rub"] += amount
                if accepted and (not agg["last_order_at"] or accepted > agg["last_order_at"]):
                    agg["last_order_at"] = accepted

        for item in own.get("ozon_products") or []:
            if not isinstance(item, dict):
                continue
            cabinet = "2" if "2" in str(item.get("cabinet") or "") else "1"
            article = str(item.get("seller_article") or "").strip()
            feed = feed_by_key.get((cabinet, article))
            orders = orders_by_key.get((cabinet, article))
            if feed:
                item.update({
                    "feed_present": True,
                    "feed_stock": feed.get("stock"),
                    "feed_reserve": feed.get("reserve"),
                    "feed_available": feed.get("available"),
                    "feed_name": feed.get("name"),
                })
            else:
                item["feed_present"] = False
            if orders:
                item.update({
                    "orders_qty_period": orders.get("orders_qty"),
                    "cancelled_qty_period": orders.get("cancelled_qty"),
                    "orders_rub_period": orders.get("orders_rub"),
                    "last_order_at": orders.get("last_order_at"),
                })
            else:
                item.update({"orders_qty_period": 0.0, "cancelled_qty_period": 0.0, "orders_rub_period": 0.0})
            if item.get("feed_present") and (item.get("orders_qty_period") or 0) > 0:
                item["operating_status"] = "selling"
            elif item.get("feed_present"):
                item["operating_status"] = "listed_no_recent_orders"
            elif (item.get("orders_qty_period") or 0) > 0:
                item["operating_status"] = "orders_present_feed_missing"
            else:
                item["operating_status"] = "mapped_but_not_in_current_feed_or_orders"

        if cabinet_statuses:
            own["ozon_cabinets"] = cabinet_statuses
        if feed_by_key:
            own["ozon_feed_products_count"] = len(feed_by_key)
        if order_values:
            own["ozon_orders_feed_rows"] = max(0, len(order_values) - 1)

    def _merge_bridge(self, payload: dict[str, Any]) -> dict[str, Any]:
        seed = self._read_json(self.seed_path) or {}
        out = self._parse_weekly(payload, seed)
        self._parse_own_27(payload, out)
        self._parse_primary_economics_axis(payload, out)
        self._parse_ozon_operating(payload, out)
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
        with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0), follow_redirects=True) as client:
            resp = client.post(url, json=request)
            resp.raise_for_status()
            payload = resp.json()
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("error") or "Sheets bridge returned error"))
        merged = self._merge_bridge(payload)
        self.live_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        return merged
