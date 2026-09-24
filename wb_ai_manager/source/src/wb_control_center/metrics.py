from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable


def fingerprint(*parts: Any) -> str:
    raw = "|".join(json.dumps(p, ensure_ascii=False, sort_keys=True, default=str) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def walk(obj: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k), v
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def count_records(obj: Any) -> int:
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        for key in ("data", "items", "result", "feedbacks", "questions", "claims", "orders", "campaigns", "adverts", "products"):
            v = obj.get(key)
            if isinstance(v, list):
                return len(v)
        return 1 if obj else 0
    return 0


def to_number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(" ", "").replace("₽", "").replace("%", "").replace(",", ".")
        m = re.fullmatch(r"-?\d+(?:\.\d+)?", s)
        if m:
            try:
                return float(s)
            except ValueError:
                pass
    return None


def first_number(obj: Any, keys: set[str]) -> float | None:
    keys = {k.lower() for k in keys}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in keys:
                n = to_number(v)
                if n is not None:
                    return n
        for v in obj.values():
            n = first_number(v, keys)
            if n is not None:
                return n
    elif isinstance(obj, list):
        for v in obj:
            n = first_number(v, keys)
            if n is not None:
                return n
    return None


def find_dicts_with_any_key(obj: Any, keys: set[str]) -> list[dict[str, Any]]:
    keys = {k.lower() for k in keys}
    out: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        if any(str(k).lower() in keys for k in obj):
            out.append(obj)
        for v in obj.values():
            out.extend(find_dicts_with_any_key(v, keys))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(find_dicts_with_any_key(v, keys))
    return out


def extract_campaign_ids(obj: Any) -> list[int]:
    ids: set[int] = set()
    for d in find_dicts_with_any_key(obj, {"advertid", "advert_id"}):
        for k in ("advertId", "advert_id"):
            if k in d:
                n = to_number(d[k])
                if n is not None and n > 0:
                    ids.add(int(n))
                    break
    return sorted(ids)


def extract_nm_id(d: dict[str, Any]) -> int | None:
    for k in ("nmID", "nmId", "nm_id", "nm"):
        if k in d:
            n = to_number(d[k])
            if n is not None:
                return int(n)
    return None


def extract_position_rows(obj: Any) -> list[tuple[int, float, str]]:
    rows = []
    for d in find_dicts_with_any_key(obj, {"position", "avgposition", "avg_position", "nmID", "nmId"}):
        nm = extract_nm_id(d)
        if nm is None:
            continue
        pos = None
        for k in ("position", "avgPosition", "avg_position", "medianPosition", "median_position"):
            if k in d:
                pos = to_number(d[k])
                if pos is not None:
                    break
        if pos is None:
            continue
        query = ""
        for k in ("searchText", "search_text", "query", "text"):
            if k in d and isinstance(d[k], str):
                query = d[k]
                break
        rows.append((nm, float(pos), query))
    return rows


def extract_ad_metrics(obj: Any) -> list[dict[str, float | int | str | None]]:
    out = []
    candidates = find_dicts_with_any_key(obj, {"advertId", "advert_id", "views", "clicks", "sum", "orders", "ctr", "cpc"})
    seen: set[str] = set()
    for d in candidates:
        advert = first_number(d, {"advertId", "advert_id", "id"})
        spend = first_number(d, {"sum", "spend", "spent", "cost", "expenses"})
        orders = first_number(d, {"orders", "ordersCount", "orders_count"})
        revenue = first_number(d, {"sum_price", "sumPrice", "revenue", "sales", "orderSum", "order_sum"})
        clicks = first_number(d, {"clicks"})
        views = first_number(d, {"views", "impressions"})
        ctr = first_number(d, {"ctr"})
        cpc = first_number(d, {"cpc"})
        if advert is None and spend is None and clicks is None:
            continue
        key = f"{advert}:{spend}:{orders}:{revenue}:{clicks}:{views}"
        if key in seen:
            continue
        seen.add(key)
        drr = (spend / revenue * 100) if spend is not None and revenue and revenue > 0 else None
        out.append({
            "advert_id": int(advert) if advert is not None else None,
            "spend": spend,
            "orders": orders,
            "revenue": revenue,
            "clicks": clicks,
            "views": views,
            "ctr": ctr,
            "cpc": cpc,
            "drr_pct": drr,
        })
    return out


def summarize_for_prompt(obj: Any, max_chars: int = 14000) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "…[truncated]"


_AD_METRIC_KEYS = {
    "sum", "spend", "spent", "cost", "expenses", "orders", "orderscount", "orders_count",
    "sum_price", "sumprice", "revenue", "sales", "ordersum", "order_sum", "clicks", "views",
    "impressions", "ctr", "cpc", "atbs", "shks"
}


def extract_ad_nm_metrics(obj: Any) -> dict[tuple[int, int], dict[str, Any]]:
    """Extract statistics for an exact (advertId, nmId) pair from WB Promotion payloads.

    WB has changed the nesting of promotion statistics several times.  The important
    invariant for money decisions is that a metric row must carry an explicit nmId.
    This parser therefore walks arbitrary nesting, inherits advert/date context, but
    never attributes campaign-level metrics to a SKU merely because a campaign contains
    that SKU.
    """
    buckets: dict[tuple[int, int], dict[str, Any]] = {}

    def direct_number(d: dict[str, Any], names: tuple[str, ...]) -> float | None:
        lowered = {str(k).lower(): v for k, v in d.items()}
        for name in names:
            if name.lower() in lowered:
                n = to_number(lowered[name.lower()])
                if n is not None:
                    return n
        return None

    def walk_ctx(x: Any, advert_ctx: int | None = None, nm_ctx: int | None = None, date_ctx: str | None = None) -> None:
        if isinstance(x, list):
            for item in x:
                walk_ctx(item, advert_ctx, nm_ctx, date_ctx)
            return
        if not isinstance(x, dict):
            return

        advert = direct_number(x, ("advertId", "advert_id"))
        if advert is None:
            advert = advert_ctx
        else:
            advert = int(advert)
        nm = direct_number(x, ("nmId", "nmID", "nm_id", "nm"))
        if nm is None:
            nm = nm_ctx
        else:
            nm = int(nm)
        date_value = x.get("date") or x.get("day") or x.get("dt") or date_ctx
        day = str(date_value)[:10] if date_value else None

        # Only direct metric keys count for this node.  This prevents a parent object
        # from double-counting metrics that merely live in nested children.
        lower_keys = {str(k).lower() for k in x.keys()}
        has_direct_metrics = bool(lower_keys & _AD_METRIC_KEYS)
        if advert and nm and has_direct_metrics:
            key = (int(advert), int(nm))
            b = buckets.setdefault(key, {
                "advertId": int(advert), "nmId": int(nm), "sum": 0.0, "orders": 0.0,
                "sum_price": 0.0, "clicks": 0.0, "views": 0.0, "days": [], "_rows": 0,
            })
            vals = {
                "sum": direct_number(x, ("sum", "spend", "spent", "cost", "expenses")),
                "orders": direct_number(x, ("orders", "ordersCount", "orders_count", "shks")),
                "sum_price": direct_number(x, ("sum_price", "sumPrice", "revenue", "sales", "orderSum", "order_sum")),
                "clicks": direct_number(x, ("clicks",)),
                "views": direct_number(x, ("views", "impressions")),
            }
            if any(v is not None for v in vals.values()):
                b["_rows"] += 1
                # If this is a dated leaf, retain it as a day row and aggregate later.
                if day:
                    dr = {"date": day}
                    for mk, mv in vals.items():
                        if mv is not None:
                            dr[mk] = float(mv)
                    b["days"].append(dr)
                else:
                    # Undated direct rows are usually period summaries.  Keep the
                    # largest observed summary rather than summing duplicate wrappers.
                    for mk, mv in vals.items():
                        if mv is not None:
                            b[mk] = max(float(b.get(mk) or 0.0), float(mv))
                ctr = direct_number(x, ("ctr",))
                cpc = direct_number(x, ("cpc",))
                if ctr is not None: b["ctr"] = float(ctr)
                if cpc is not None: b["cpc"] = float(cpc)

        for v in x.values():
            if isinstance(v, (dict, list)):
                walk_ctx(v, advert, nm, day)

    walk_ctx(obj)

    for b in buckets.values():
        days = b.get("days") or []
        if days:
            # Merge duplicate same-day leaves (e.g. placements/clusters) by sum.
            merged: dict[str, dict[str, Any]] = {}
            for d in days:
                dt = str(d.get("date") or "")
                m = merged.setdefault(dt, {"date": dt, "sum": 0.0, "orders": 0.0, "sum_price": 0.0, "clicks": 0.0, "views": 0.0})
                for k in ("sum", "orders", "sum_price", "clicks", "views"):
                    if d.get(k) is not None:
                        m[k] += float(d[k])
            b["days"] = [merged[k] for k in sorted(merged)]
            totals = {k: sum(float(d.get(k) or 0) for d in b["days"]) for k in ("sum", "orders", "sum_price", "clicks", "views")}
            # Dated SKU rows are more trustworthy than an ambiguous wrapper.
            for k, v in totals.items():
                if v > 0 or not b.get(k):
                    b[k] = v
        views = float(b.get("views") or 0)
        clicks = float(b.get("clicks") or 0)
        spend = float(b.get("sum") or 0)
        b["ctr"] = float(b.get("ctr")) if b.get("ctr") is not None else ((clicks / views * 100) if views > 0 else None)
        b["cpc"] = float(b.get("cpc")) if b.get("cpc") is not None else ((spend / clicks) if clicks > 0 else None)
        b.pop("_rows", None)
    return buckets
