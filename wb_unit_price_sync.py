import json, os, sys, time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.parse import urlencode as form_urlencode
from urllib.error import HTTPError, URLError

API = "https://discounts-prices-api.wildberries.ru/api/v2/list/goods/filter"
SHOPS = [
    ("AP", "Саныч", "WB_API_TOKEN_AP", 95),
    ("AA", "AIR", "WB_API_TOKEN_AA", 83),
    ("YV", "Хозяюшка", "WB_API_TOKEN_YV", 62),
]
MIN_TOTAL = 240
MAX_TOTAL = 1200
LIMIT = 1000

def request_json(url, token, params=None):
    if params:
        url = url + "?" + urlencode(params)
    req = Request(url, headers={"Authorization": token})
    for attempt in range(4):
        try:
            with urlopen(req, timeout=60) as res:
                return json.loads(res.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(10 * (attempt + 1))
                continue
            detail = e.read(500).decode("utf-8", "replace")
            raise RuntimeError(f"WB HTTP {e.code}: {detail}")
        except (URLError, TimeoutError) as e:
            if attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
            raise RuntimeError(f"WB connection failed: {e}")
    raise RuntimeError("WB request exhausted retries")

def number_or_blank(value):
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        raise RuntimeError("Unexpected boolean numeric field")
    return float(value)

def fetch_shop(code, label, secret_name, floor):
    token = os.environ.get(secret_name, "").strip()
    if not token:
        raise RuntimeError(f"Missing required secret {secret_name}")
    goods = []
    offset = 0
    while True:
        result = request_json(API, token, {"limit": LIMIT, "offset": offset})
        data = result.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("listGoods"), list):
            raise RuntimeError(f"{code}: malformed WB response")
        batch = data["listGoods"]
        goods.extend(batch)
        if len(batch) < LIMIT:
            break
        offset += len(batch)
        if offset > 100000:
            raise RuntimeError(f"{code}: pagination safety limit")
    rows = []
    seen = set()
    for item in goods:
        if not isinstance(item, dict) or not item.get("nmID"):
            raise RuntimeError(f"{code}: malformed product record")
        nm = int(item["nmID"])
        if nm in seen:
            raise RuntimeError(f"{code}: duplicate nmID {nm}")
        seen.add(nm)
        sizes = item.get("sizes") or []
        size = sizes[0] if sizes else {}
        if not isinstance(size, dict):
            raise RuntimeError(f"{code}: malformed size record for {nm}")
        price = number_or_blank(size.get("price"))
        discount = number_or_blank(item.get("discount"))
        discounted = size.get("discountedPrice")
        if discounted in (None, "") and price != "" and discount != "":
            discounted = round(price * (1 - discount / 100), 2)
        club_discount = number_or_blank(item.get("clubDiscount"))
        club_price = number_or_blank(size.get("clubDiscountedPrice"))
        if club_price == "" and discounted != "" and club_discount != "":
            club_price = round(float(discounted) * (1 - club_discount / 100), 2)
        status = "цена есть" if price != "" else "нет цены WB"
        vendor = str(item.get("vendorCode") or "").strip()
        title = vendor
        tech = str(size.get("techSizeName") or "")
        rows.append([
            code, label, nm, vendor, title, discount, discounted if discounted is not None else "",
            club_discount, club_price, status, tech, price,
            str(item.get("currency") or "RUB")
        ])
    if len(rows) < floor:
        raise RuntimeError(f"{code}: only {len(rows)} products; minimum safe count is {floor}")
    return rows

def google_access_token():
    raw = os.environ.get("CLASPRC_JSON", "")
    if not raw:
        raise RuntimeError("Missing Google authorization secret")
    try:
        config = json.loads(raw)
        client = config["oauth2ClientSettings"]
        refresh = config["token"]["refresh_token"]
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("Google authorization secret has an unexpected format")
    form = form_urlencode({
        "client_id": client["clientId"],
        "client_secret": client["clientSecret"],
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    }).encode()
    req = Request("https://oauth2.googleapis.com/token", data=form,
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(req, timeout=30) as res:
            token_data = json.loads(res.read().decode("utf-8"))
    except Exception:
        raise RuntimeError("Google authorization refresh failed")
    token = token_data.get("access_token")
    if not token:
        raise RuntimeError("Google authorization refresh returned no access token")
    return token

def post_bridge(url, payload, access_token):
    req = Request(url, data=json.dumps(payload, ensure_ascii=False).encode(),
                  headers={"Content-Type": "application/json", "Authorization": "Bearer " + access_token},
                  method="POST")
    try:
        with urlopen(req, timeout=120) as res:
            body = res.read().decode("utf-8")
    except HTTPError as e:
        detail = e.read(500).decode("utf-8", "replace")
        raise RuntimeError(f"Bridge HTTP {e.code}: {detail}")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError("Bridge returned non-JSON response")

def main():
    bridge = os.environ.get("BRIDGE_URL", "").strip()
    key = os.environ.get("WB_UNIT_PRICE_BRIDGE_KEY", "").strip()
    if not bridge.startswith("https://script.google.com/macros/s/") or not bridge.endswith("/exec"):
        raise RuntimeError("Invalid bridge URL")
    if not key:
        raise RuntimeError("Missing bridge authorization key")
    access_token = google_access_token()
    snapshot = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    rows = []
    counts = {}
    for code, label, secret, floor in SHOPS:
        shop_rows = fetch_shop(code, label, secret, floor)
        counts[code] = len(shop_rows)
        rows.extend([[snapshot] + r for r in shop_rows])
    total = len(rows)
    if total < MIN_TOTAL or total > MAX_TOTAL:
        raise RuntimeError(f"Unsafe total product count: {total}")
    if len({(row[1], row[3]) for row in rows}) != total:
        raise RuntimeError("Duplicate store/nmID pair detected")
    data = {"key": key, "action": "replace_snapshot", "snapshot_at": snapshot, "rows": rows}
    saved = post_bridge(bridge, data, access_token)
    if saved.get("ok") is not True or int(saved.get("updated_cells", -1)) != total * 14:
        raise RuntimeError(f"Snapshot write was not confirmed: {saved}")
    verified = post_bridge(bridge, {"key": key, "action": "verify_snapshot"}, access_token)
    if verified.get("ok") is not True or verified.get("snapshot_at") != snapshot or int(verified.get("rows", -1)) != total:
        raise RuntimeError(f"Snapshot reread failed: {verified}")
    print(json.dumps({"ok": True, "snapshot_at": snapshot, "counts": counts, "total": total, "verified": True}, ensure_ascii=False))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
