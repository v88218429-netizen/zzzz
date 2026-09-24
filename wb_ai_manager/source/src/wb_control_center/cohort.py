from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Iterable


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        d = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def _q(values: list[float], p: float) -> float | None:
    if not values:
        return None
    a = sorted(values)
    pos = (len(a) - 1) * p
    lo = int(pos)
    hi = min(len(a) - 1, lo + 1)
    frac = pos - lo
    return a[lo] + (a[hi] - a[lo]) * frac


def _cdf(values: list[float], age_days: float) -> float | None:
    if not values:
        return None
    age = max(0.0, age_days)
    return sum(1 for x in values if x <= age) / len(values)


@dataclass
class CohortLagSummary:
    count: int
    p25_days: float | None
    p50_days: float | None
    p75_days: float | None
    p90_days: float | None
    p95_days: float | None
    mean_days: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CohortWindowSummary:
    days: int
    orders: int
    buyouts_observed: int
    cancels_observed: int
    open_orders: int
    observed_buyout_pct: float | None
    observed_cancel_pct: float | None
    maturity_pct: float | None
    quality_ready: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProductCohortSummary:
    sku: str
    buyout_lag: CohortLagSummary
    cancel_lag: CohortLagSummary
    recent_3d: CohortWindowSummary
    recent_7d: CohortWindowSummary
    unresolved_older_than_p90: int
    as_of: str | None
    note: str

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        return out


def lag_summary(values: list[float]) -> CohortLagSummary:
    return CohortLagSummary(
        count=len(values),
        p25_days=round(_q(values, 0.25), 2) if values else None,
        p50_days=round(_q(values, 0.50), 2) if values else None,
        p75_days=round(_q(values, 0.75), 2) if values else None,
        p90_days=round(_q(values, 0.90), 2) if values else None,
        p95_days=round(_q(values, 0.95), 2) if values else None,
        mean_days=round(mean(values), 2) if values else None,
    )


def _window(rows: list[dict[str, Any]], as_of: datetime, days: int, buyout_lags: list[float]) -> CohortWindowSummary:
    selected = []
    for row in rows:
        od = row.get("order_dt")
        if not isinstance(od, datetime):
            continue
        age = (as_of - od).total_seconds() / 86400.0
        if 0 <= age <= days:
            selected.append(row)
    orders = len(selected)
    buyouts = sum(1 for x in selected if x.get("status") == "buyout")
    cancels = sum(1 for x in selected if x.get("status") == "cancel")
    open_orders = sum(1 for x in selected if x.get("status") == "created")
    maturity_parts: list[float] = []
    if buyout_lags:
        for row in selected:
            od = row.get("order_dt")
            if not isinstance(od, datetime):
                continue
            age = (as_of - od).total_seconds() / 86400.0
            c = _cdf(buyout_lags, age)
            if c is not None:
                maturity_parts.append(c)
    maturity = mean(maturity_parts) if maturity_parts else None
    return CohortWindowSummary(
        days=days,
        orders=orders,
        buyouts_observed=buyouts,
        cancels_observed=cancels,
        open_orders=open_orders,
        observed_buyout_pct=(round(buyouts / orders * 100, 2) if orders else None),
        observed_cancel_pct=(round(cancels / orders * 100, 2) if orders else None),
        maturity_pct=(round(maturity * 100, 2) if maturity is not None else None),
        quality_ready=bool(maturity is not None and maturity >= 0.80 and orders >= 10),
    )


def analyze_order_lifecycle(values: list[list[Any]]) -> dict[str, Any]:
    """Analyze WB lifecycle feed rows.

    Expected headers: Дата заказа, Статус, Артикул WB, Дата изменения, Выгружено.
    Each SRID should be represented by its current lifecycle state. Fresh order cohorts are
    never judged by raw buyout percentages until their empirical maturity is high enough.
    """
    if not values:
        return {"as_of": None, "global": {}, "by_sku": {}}
    headers = [str(x or "").strip() for x in values[0]]
    idx = {h: i for i, h in enumerate(headers)}
    required = {"Дата заказа", "Статус", "Артикул WB", "Дата изменения"}
    if not required.issubset(idx):
        return {"as_of": None, "global": {}, "by_sku": {}, "error": "missing lifecycle headers"}

    parsed: list[dict[str, Any]] = []
    as_of_candidates: list[datetime] = []
    seen_srid: set[str] = set()
    for row in values[1:]:
        def cell(name: str) -> Any:
            i = idx.get(name)
            return row[i] if i is not None and i < len(row) else None
        srid = str(cell("SRID") or "").strip()
        # Keep only one current state per SRID if duplicate snapshots ever appear.
        if srid and srid in seen_srid:
            continue
        if srid:
            seen_srid.add(srid)
        od = _dt(cell("Дата заказа"))
        changed = _dt(cell("Дата изменения"))
        exported = _dt(cell("Выгружено"))
        if exported:
            as_of_candidates.append(exported)
        elif changed:
            as_of_candidates.append(changed)
        status = str(cell("Статус") or "").strip().lower()
        sku = str(cell("Артикул WB") or "").strip()
        if not od or status not in {"created", "buyout", "cancel"}:
            continue
        parsed.append({"sku": sku, "status": status, "order_dt": od, "change_dt": changed, "srid": srid})

    as_of = max(as_of_candidates) if as_of_candidates else datetime.now(timezone.utc)
    global_buyout_lags: list[float] = []
    global_cancel_lags: list[float] = []
    by_sku_rows: dict[str, list[dict[str, Any]]] = {}
    for row in parsed:
        sku = row["sku"]
        by_sku_rows.setdefault(sku, []).append(row)
        changed = row.get("change_dt")
        if isinstance(changed, datetime):
            lag = (changed - row["order_dt"]).total_seconds() / 86400.0
            if 0 <= lag <= 90:
                if row["status"] == "buyout":
                    global_buyout_lags.append(lag)
                elif row["status"] == "cancel":
                    global_cancel_lags.append(lag)

    global_buyout = lag_summary(global_buyout_lags)
    global_cancel = lag_summary(global_cancel_lags)
    by_sku: dict[str, Any] = {}
    for sku, rows in by_sku_rows.items():
        bl: list[float] = []
        cl: list[float] = []
        for row in rows:
            changed = row.get("change_dt")
            if not isinstance(changed, datetime):
                continue
            lag = (changed - row["order_dt"]).total_seconds() / 86400.0
            if 0 <= lag <= 90:
                if row["status"] == "buyout":
                    bl.append(lag)
                elif row["status"] == "cancel":
                    cl.append(lag)
        # SKU-specific lags are used only with enough observations; otherwise use the store distribution.
        maturity_lags = bl if len(bl) >= 20 else global_buyout_lags
        bsum = lag_summary(bl if bl else global_buyout_lags)
        csum = lag_summary(cl if cl else global_cancel_lags)
        p90 = bsum.p90_days or global_buyout.p90_days or 14.0
        unresolved_old = 0
        for row in rows:
            if row["status"] != "created":
                continue
            age = (as_of - row["order_dt"]).total_seconds() / 86400.0
            if age > p90:
                unresolved_old += 1
        summary = ProductCohortSummary(
            sku=sku,
            buyout_lag=bsum,
            cancel_lag=csum,
            recent_3d=_window(rows, as_of, 3, maturity_lags),
            recent_7d=_window(rows, as_of, 7, maturity_lags),
            unresolved_older_than_p90=unresolved_old,
            as_of=as_of.isoformat(),
            note=(
                "Свежие заказы оцениваются как спрос. Выкуп/отмена используются для оценки качества только после "
                "поправки на фактический лаг созревания этой товарной когорты."
            ),
        )
        by_sku[sku] = summary.to_dict()

    return {
        "as_of": as_of.isoformat(),
        "rows": len(parsed),
        "global": {
            "buyout_lag": global_buyout.to_dict(),
            "cancel_lag": global_cancel.to_dict(),
            "recent_3d": _window(parsed, as_of, 3, global_buyout_lags).to_dict(),
            "recent_7d": _window(parsed, as_of, 7, global_buyout_lags).to_dict(),
        },
        "by_sku": by_sku,
    }
