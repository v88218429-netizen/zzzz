#!/usr/bin/env python3
"""Generate non-secret WB search snapshots for GitHub publishing.

AIR uses the official Seller Analytics search-report API when available.
Cabinets without a Jam subscription fall back to factual public WB SERP ranks.
No token value is ever written to output.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

PRICE_URL = "https://discounts-prices-api.wildberries.ru/api/v2/list/goods/filter"
CONTENT_URL = "https://content-api.wildberries.ru/content/v2/get/cards/list"
ANALYTICS_SEARCH_URL = "https://seller-analytics-api.wildberries.ru/api/v2/search-report/product/search-texts"
PUBLIC_SEARCH_URLS = (
    "https://search.wb.ru/exactmatch/ru/common/v4/search",
    "https://search.wb.ru/exactmatch/ru/common/v5/search",
    "https://www.wildberries.ru/__internal/search/exactmatch/ru/common/v18/search",
)
SHOPS = {
    "air": ("AIR", "WB_API_TOKEN_AA"),
    "hozyushka": ("Хозяюшка", "WB_API_TOKEN_YV"),
}


def seller_request(
    url: str,
    token: str,
    *,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    base_headers = {"Accept": "application/json"}
    if data is not None:
        base_headers["Content-Type"] = "application/json"

    last: Exception | None = None
    for auth in (token, f"Bearer {token}"):
        headers = dict(base_headers)
        headers["Authorization"] = auth
        for attempt in range(6):
            try:
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers=headers,
                    method="POST" if data is not None else "GET",
                )
                with urllib.request.urlopen(req, timeout=120) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 402, 403):
                    detail = exc.read().decode("utf-8", errors="replace")[:500]
                    last = RuntimeError(f"HTTP {exc.code}: {detail}")
                    break
                if exc.code == 429 or 500 <= exc.code <= 599:
                    time.sleep(min(45, 3 * (2**attempt)))
                    continue
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last = exc
                if attempt < 5:
                    time.sleep(min(30, 2 * (2**attempt)))
    raise RuntimeError(f"request failed: {last}")


def public_search(query: str, page: int = 1) -> dict[str, Any]:
    params = {
        "appType": 1,
        "curr": "rub",
        "dest": -1257786,
        "query": query,
        "resultset": "catalog",
        "limit": 100,
        "page": page,
        "sort": "popular",
        "spp": 30,
        "suppressSpellcheck": "false",
    }
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/129 Safari/537.36",
        "Referer": "https://www.wildberries.ru/",
    }
    last: Exception | None = None
    for base in PUBLIC_SEARCH_URLS:
        url = base + "?" + urllib.parse.urlencode(params)
        for attempt in range(5):
            try:
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=60) as response:
                    raw = response.read().decode("utf-8")
                    obj = json.loads(raw) if raw else {}
                if _products(obj) or _total(obj) == 0:
                    return obj
                last = RuntimeError(f"unexpected WB public search shape from {base}")
                break
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code == 429 or 500 <= exc.code <= 599:
                    retry_after = exc.headers.get("Retry-After")
                    try:
                        wait = max(1.0, float(retry_after)) if retry_after else min(20.0, 1.5 * (2**attempt))
                    except ValueError:
                        wait = min(20.0, 1.5 * (2**attempt))
                    time.sleep(wait)
                    continue
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = exc
                if attempt < 4:
                    time.sleep(min(12.0, 1.5 * (2**attempt)))
        time.sleep(0.4)
    raise RuntimeError(f"public WB search failed: {type(last).__name__}: {last}")


def _products(obj: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(obj, dict):
        return []
    candidates: list[Any] = [obj.get("products")]
    data = obj.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("products"), data.get("items")])
    result = obj.get("result")
    if isinstance(result, dict):
        candidates.extend([result.get("products"), result.get("items")])
    for value in candidates:
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def _total(obj: dict[str, Any]) -> int | None:
    for holder in (obj, obj.get("data") if isinstance(obj, dict) else None):
        if isinstance(holder, dict):
            raw = holder.get("total")
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
    return None


def product_id(product: dict[str, Any]) -> int | None:
    for key in ("id", "nmId", "nmID"):
        try:
            value = int(product.get(key))
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return None


def product_name(product: dict[str, Any]) -> str:
    return str(product.get("name") or product.get("title") or "").strip()


def nm_ids(token: str) -> list[int]:
    obj = seller_request(PRICE_URL, token, params={"limit": 1000, "offset": 0})
    data = obj.get("data", obj) if isinstance(obj, dict) else {}
    goods = (data.get("listGoods") or data.get("goods") or []) if isinstance(data, dict) else []
    out: list[int] = []
    for item in goods:
        try:
            out.append(int(item.get("nmID") or item.get("nmId")))
        except (TypeError, ValueError, AttributeError):
            pass
    return sorted(set(value for value in out if value > 0))


def content_titles(token: str, wanted_ids: list[int]) -> dict[int, str]:
    wanted = set(wanted_ids)
    titles: dict[int, str] = {}
    cursor: dict[str, Any] = {"limit": 100}
    for _ in range(10):
        payload = {
            "settings": {
                "sort": {"ascending": False},
                "filter": {"withPhoto": -1},
                "cursor": cursor,
            }
        }
        obj = seller_request(CONTENT_URL, token, payload=payload)
        cards = obj.get("cards") if isinstance(obj, dict) else None
        if not isinstance(cards, list) or not cards:
            break
        for card in cards:
            if not isinstance(card, dict):
                continue
            try:
                nm = int(card.get("nmID") or card.get("nmId"))
            except (TypeError, ValueError):
                continue
            if nm not in wanted:
                continue
            title = str(card.get("title") or card.get("subjectName") or card.get("vendorCode") or "").strip()
            if title:
                titles[nm] = title
        if wanted.issubset(titles.keys()) or len(cards) < 100:
            break
        c = obj.get("cursor") if isinstance(obj, dict) else {}
        if not isinstance(c, dict) or not c.get("updatedAt") or not c.get("nmID"):
            break
        cursor = {"limit": 100, "updatedAt": c["updatedAt"], "nmID": c["nmID"]}
    return titles


def public_titles(wanted_ids: list[int], cap: int = 60) -> dict[int, str]:
    titles: dict[int, str] = {}
    for idx, nm in enumerate(wanted_ids[:cap], start=1):
        obj = public_search(str(nm), page=1)
        for product in _products(obj):
            if product_id(product) == nm:
                title = product_name(product)
                if title:
                    titles[nm] = title
                break
        if idx < min(cap, len(wanted_ids)):
            time.sleep(0.35)
    return titles


def periods() -> tuple[str, str, str, str]:
    end = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=6)
    past_end = start - dt.timedelta(days=1)
    past_start = past_end - dt.timedelta(days=6)
    return start.isoformat(), end.isoformat(), past_start.isoformat(), past_end.isoformat()


def analytics_snapshot(token: str, ids: list[int]) -> dict[str, Any]:
    start, end, past_start, past_end = periods()
    rows: dict[str, dict[str, Any]] = {}
    for off in range(0, len(ids), 50):
        payload = {
            "currentPeriod": {"start": start, "end": end},
            "pastPeriod": {"start": past_start, "end": past_end},
            "nmIds": ids[off : off + 50],
            "topOrderBy": "openCard",
            "includeSubstitutedSKUs": True,
            "includeSearchTexts": True,
            "orderBy": {"field": "avgPosition", "mode": "asc"},
            "limit": 30,
        }
        obj = seller_request(ANALYTICS_SEARCH_URL, token, payload=payload)
        items = ((obj.get("data") or {}).get("items") or []) if isinstance(obj, dict) else []
        for item in items:
            try:
                nm = int(item.get("nmId") or item.get("nmID"))
                query = str(item.get("text") or "").strip()
                position = float((item.get("avgPosition") or {}).get("current"))
            except (TypeError, ValueError, AttributeError):
                continue
            if not query:
                continue
            raw_frequency = (item.get("frequency") or {}).get("current")
            try:
                frequency: float | None = float(raw_frequency) if raw_frequency not in (None, "") else None
            except (TypeError, ValueError):
                frequency = None
            key = f"{nm}|{query.casefold()}"
            rows[key] = {
                "source": "wb_search_report",
                "nm_id": nm,
                "name": str(item.get("name") or "").strip(),
                "query": query,
                "position": position,
                "frequency": frequency,
            }
        if off + 50 < len(ids):
            time.sleep(2)
    if not rows:
        raise RuntimeError("zero factual search rows")
    return {
        "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "period_end": end,
        "trust_status": "FACTUAL_WB_ANALYTICS",
        "data": rows,
    }


def normalize_query(title: str) -> str:
    text = title.casefold().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я]+", " ", text)
    # Remove pack/measurement details; keep product-defining numbers when they are not units.
    text = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:шт|штук|кг|г|л|мл|м|см|мм)\b", " ", text)
    text = re.sub(r"\b(?:набор|комплект|упаковка|уп)\b", " ", text)
    tokens = [t for t in text.split() if len(t) > 1 and t not in {"для", "из", "на", "по", "с"}]
    return " ".join(tokens[:7]).strip()


def public_serp_snapshot(token: str, ids: list[int]) -> dict[str, Any]:
    try:
        titles = content_titles(token, ids)
        title_source = "WB_CONTENT_API"
    except Exception as exc:
        print(f"Content API title lookup unavailable: {type(exc).__name__}; using public product lookup")
        titles = {}
        title_source = "WB_PUBLIC_SEARCH_ID_LOOKUP"

    if len(titles) < min(12, len(ids)):
        fallback = public_titles([nm for nm in ids if nm not in titles], cap=60)
        titles.update(fallback)

    if not titles:
        raise RuntimeError("public SERP fallback could not resolve product titles")

    # Keep runtime bounded but broad enough to be decision-usable.
    selected = list(titles.items())[:60]
    query_to_ids: dict[str, set[int]] = {}
    for nm, title in selected:
        query = normalize_query(title)
        if len(query) < 3:
            continue
        query_to_ids.setdefault(query, set()).add(nm)

    rows: dict[str, dict[str, Any]] = {}
    for q_idx, (query, target_ids) in enumerate(query_to_ids.items(), start=1):
        found: dict[int, tuple[int, str]] = {}
        for page in (1, 2):
            obj = public_search(query, page=page)
            products = _products(obj)
            if not products:
                break
            for index, product in enumerate(products):
                nm = product_id(product)
                if nm in target_ids and nm not in found:
                    found[nm] = ((page - 1) * len(products) + index + 1, product_name(product))
            if target_ids.issubset(found.keys()):
                break
            time.sleep(0.25)
        for nm, (position, public_name) in found.items():
            key = f"{nm}|{query.casefold()}"
            rows[key] = {
                "source": "wb_public_search",
                "nm_id": nm,
                "name": public_name or titles.get(nm, ""),
                "query": query,
                "position": position,
                "frequency": None,
            }
        if q_idx < len(query_to_ids):
            time.sleep(0.35)

    if len(rows) < 10:
        raise RuntimeError(f"public SERP fallback yielded too few factual rows: {len(rows)}")

    _, end, _, _ = periods()
    return {
        "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "period_end": end,
        "trust_status": "FACTUAL_PUBLIC_SERP",
        "title_source": title_source,
        "frequency_available": False,
        "data": rows,
    }


def build_snapshot(cabinet: str, token: str, ids: list[int]) -> dict[str, Any]:
    try:
        return analytics_snapshot(token, ids)
    except RuntimeError as exc:
        message = str(exc)
        if "Jam subscription" not in message:
            raise
        print(f"{cabinet}: seller Analytics requires Jam; switching to factual public SERP fallback")
        try:
            return public_serp_snapshot(token, ids)
        except Exception as fallback_error:
            # Do not keep probing blocked public endpoints or turn an access
            # denial into guessed positions. The caller records an explicit
            # unavailable state while allowing other cabinets to continue.
            message = str(fallback_error).casefold()
            code = "JAM_REQUIRED_PUBLIC_SERP_BLOCKED" if "403" in message or "forbidden" in message else "JAM_REQUIRED_PUBLIC_SERP_UNAVAILABLE"
            raise RuntimeError(code) from fallback_error


def _unavailable_code(exc: Exception) -> str:
    message = str(exc).casefold()
    if "jam_required_public_serp_blocked" in message:
        return "JAM_REQUIRED_PUBLIC_SERP_BLOCKED"
    if "jam_required_public_serp_unavailable" in message:
        return "JAM_REQUIRED_PUBLIC_SERP_UNAVAILABLE"
    if "jam subscription" in message or "available only in a jam" in message:
        return "JAM_REQUIRED"
    if "public wb search failed" in message or "http error 403" in message:
        return "PUBLIC_SEARCH_ACCESS_DENIED"
    if "token_missing" in message:
        return "TOKEN_MISSING"
    if "no nmid" in message:
        return "PRODUCT_CATALOG_EMPTY"
    return "SOURCE_ERROR"


def collect_snapshots(out: pathlib.Path) -> dict[str, Any]:
    """Collect cabinets independently; a blocked shop must not discard good facts."""
    out.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(), "cabinets": {}}
    _, period_end, _, _ = periods()
    available = 0
    for cabinet, (name, env_name) in SHOPS.items():
        token = os.environ.get(env_name, "").strip()
        if not token:
            error = RuntimeError("TOKEN_MISSING")
            ids = []
            snap = None
        else:
            try:
                ids = nm_ids(token)
                if not ids:
                    raise RuntimeError("no nmIds")
                print(f"{name}: discovered {len(ids)} nmIds")
                snap = build_snapshot(cabinet, token, ids)
                if snap.get("trust_status") not in {"FACTUAL_WB_ANALYTICS", "FACTUAL_PUBLIC_SERP"} or not snap.get("data"):
                    raise RuntimeError("snapshot failed factual-data validation")
                available += 1
                error = None
            except Exception as exc:
                error = exc
                snap = None

        state: dict[str, Any]
        if snap is None:
            snap = {
                "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                "period_end": period_end,
                "trust_status": "UNAVAILABLE",
                "data": {},
            }
            state = {
                "name": name,
                "status": "UNAVAILABLE",
                "query_rows": 0,
                "period_end": period_end,
                "created_at": snap["created_at"],
                "reason_code": _unavailable_code(error or RuntimeError("unknown source error")),
            }
            print(f"{cabinet}: UNAVAILABLE reason={state['reason_code']}; no rows published")
        else:
            state = {
                "name": name,
                "status": "FACTUAL",
                "nm_ids": len(ids),
                "query_rows": len(snap["data"]),
                "period_end": snap["period_end"],
                "created_at": snap["created_at"],
                "trust_status": snap["trust_status"],
            }
            print(f"{cabinet}: query_rows={len(snap['data'])} trust={snap['trust_status']}")
        (out / f"{cabinet}.json").write_text(
            json.dumps(snap, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest["cabinets"][cabinet] = state
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {**manifest, "available_cabinets": available}


def publish_snapshots(out: pathlib.Path, bridge_url: str, bridge_key: str) -> dict[str, int]:
    """Send only factual rows to the authenticated Sheets bridge, never Git."""
    if not bridge_url or not bridge_key:
        raise RuntimeError("authenticated Sheets bridge configuration missing")
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for cabinet, (name, _) in SHOPS.items():
        snap = json.loads((out / f"{cabinet}.json").read_text(encoding="utf-8"))
        state = (manifest.get("cabinets") or {}).get(cabinet, {})
        if state.get("status") != "FACTUAL":
            print(f"{cabinet}: publication skipped; source={state.get('reason_code', 'UNAVAILABLE')}")
            continue
        rows = snap.get("data") or {}
        if snap.get("trust_status") not in {"FACTUAL_WB_ANALYTICS", "FACTUAL_PUBLIC_SERP"} or not rows:
            raise RuntimeError(f"{cabinet}: refusing to publish non-factual or empty snapshot")
        body = json.dumps({
            "token": bridge_key,
            "action": "publish_search_positions",
            "cabinet": cabinet,
            "snapshot": snap,
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            bridge_url,
            data=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                result = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"{cabinet}: authenticated search publication failed: {type(exc).__name__}") from exc
        written = int(result.get("search_rows") or 0) if isinstance(result, dict) else 0
        if not isinstance(result, dict) or result.get("ok") is not True or result.get("skipped") or written != len(rows):
            raise RuntimeError(f"{cabinet}: search publication row-count or acceptance mismatch")
        counts[cabinet] = written
        print(f"{cabinet}: published factual position rows={written}; source={snap['trust_status']}")
    return counts


def require_all_sources(out: pathlib.Path) -> None:
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    states = manifest.get("cabinets") or {}
    unavailable = [f"{cabinet}:{(states.get(cabinet) or {}).get('reason_code', 'MISSING')}"
                   for cabinet in SHOPS if (states.get(cabinet) or {}).get("status") != "FACTUAL"]
    if unavailable:
        raise RuntimeError("search source acceptance failed: " + ", ".join(unavailable))


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", help="publish factual snapshots through the authenticated Sheets bridge")
    parser.add_argument("--require-all", action="store_true", help="fail unless both cabinets have factual search rows")
    args = parser.parse_args(argv)
    out = pathlib.Path(os.environ.get("SEARCH_GITHUB_OUT", "wb_data/search"))
    if args.publish:
        publish_snapshots(
            out,
            os.environ.get("GOOGLE_SHEETS_BRIDGE_URL", "").strip(),
            os.environ.get("GOOGLE_SHEETS_BRIDGE_KEY", "").strip(),
        )
        return 0
    if args.require_all:
        require_all_sources(out)
        print("WB_SEARCH_SNAPSHOT_ACCEPTANCE=FACTUAL_ALL_CABINETS")
        return 0
    result = collect_snapshots(out)
    print(json.dumps({
        "manifest": result,
        "all_cabinets_factual": result["available_cabinets"] == len(SHOPS),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if result["available_cabinets"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
