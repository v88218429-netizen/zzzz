import base64
import datetime as dt
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BASE = "https://api-performance.ozon.ru"
PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----
MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEA2Ybi/Sni0sReVYP+zl1f
0+2zFh5+Qjqtv5YxPp8AHirqEoT+rYsVYpTOQcdmJWcYyMMSdaQIuHrKLYXDCJhM
fiHEzixNrIxaFp17Rl6XkiEsZKc8v2PhUTzNvejhWMcFiFnqzOTz73RFFzk+ZPwO
dhzXpi5ccmFPD8/GB3D5vazid/7JWVhUhQJgz1ZjvF+hWHawYxwIEgWng0L+Zu02
nRrOt70lOBQljbghb7rf2pYVvEiSlB+wS2AFCFK+Uh4zWZ6N8cfAPZGB4ZEoUZd8
5d7qsO7TecQcIlvr30yNnXxN3M8mQ5rwYFvkUKLgDyL1QiVS+UQbDtTZD8YzvGPw
Fy2YuXZup9vAt7vx3NPhYd/owrI2cvMArna805ca1L5YGVX1hIHU6n5bpl5dOjuo
09uXODf83RrZvrqNDTQTYuEyFSZvG+5rgfsmF3UOXrVYShmfBaCZOYgjQYH3YWPc
wNpLdjGq/8FNoBfwGBvIkS2XGwh68Z/MLIvzjf7j8pU1AgMBAAE=
-----END PUBLIC KEY-----"""
AAD = b"OZON_PERFORMANCE_EXPORT_V1"
FIRST_DATE = dt.date(2025, 2, 1)
WINDOW_DAYS = 60
STAT_ENDPOINTS = (
    "/api/client/statistics/daily/json",
    "/api/client/statistics/campaign/product/json",
)


def windows(first, last):
    start = first
    while start <= last:
        end = min(start + dt.timedelta(days=WINDOW_DAYS - 1), last)
        yield start, end
        start = end + dt.timedelta(days=1)


def encrypt_bundle(bundle):
    public = serialization.load_pem_public_key(PUBLIC_KEY)
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    packed = gzip.compress(
        json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        compresslevel=9,
    )
    ciphertext = AESGCM(key).encrypt(nonce, packed, AAD)
    wrapped_key = public.encrypt(
        key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    envelope = {
        "v": 1,
        "k": base64.b64encode(wrapped_key).decode("ascii"),
        "n": base64.b64encode(nonce).decode("ascii"),
        "c": base64.b64encode(ciphertext).decode("ascii"),
    }
    return base64.urlsafe_b64encode(
        json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def fetch_json(session, path, params):
    last_status = None
    for attempt in range(4):
        try:
            response = session.get(
                BASE + path,
                params=params,
                timeout=45,
            )
            last_status = response.status_code
            if response.status_code == 401:
                return None, 401
            if response.status_code == 429 or response.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code >= 400:
                return None, response.status_code
            try:
                return response.json(), response.status_code
            except Exception:
                return None, response.status_code
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None, last_status or 0


def collect_store(store, client_id, secret, last_date):
    bundle = {
        "store": store,
        "period_start": FIRST_DATE.isoformat(),
        "period_end": last_date.isoformat(),
        "daily": [],
        "campaign_product": [],
        "errors": [],
    }
    if not client_id or not secret:
        bundle["errors"].append({"stage": "credentials", "status": "missing"})
        return bundle

    try:
        auth = requests.post(
            BASE + "/api/client/token",
            json={
                "client_id": client_id,
                "client_secret": secret,
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        if auth.status_code >= 400:
            bundle["errors"].append({"stage": "auth", "status": auth.status_code})
            return bundle
        token = auth.json().get("access_token")
        if not token:
            bundle["errors"].append({"stage": "auth", "status": "token_missing"})
            return bundle
    except Exception:
        bundle["errors"].append({"stage": "auth", "status": "request_failed"})
        return bundle

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
    })
    campaign_ids = []
    page = 1
    while page <= 20:
        campaign_data, campaign_status = fetch_json(
            session,
            "/api/client/campaign",
            {"page": page, "pageSize": 100, "advObjectType": "SKU"},
        )
        if campaign_data is None:
            bundle["errors"].append({"stage": "campaign_list", "status": campaign_status})
            break
        campaign_rows = campaign_data.get("list") or []
        campaign_ids.extend(
            str(row.get("id")) for row in campaign_rows if row.get("id") is not None
        )
        if len(campaign_rows) < 100:
            break
        page += 1

    for path in STAT_ENDPOINTS:
        key = "daily" if path.endswith("/daily/json") else "campaign_product"
        requested_start = FIRST_DATE
        if key == "campaign_product":
            observed_dates = []
            for daily_window in bundle["daily"]:
                for row in daily_window["response"].get("rows", []):
                    try:
                        observed_dates.append(dt.date.fromisoformat(str(row.get("date"))))
                    except (TypeError, ValueError):
                        pass
            if observed_dates:
                requested_start = min(observed_dates)
        for start, end in windows(requested_start, last_date):
            params = {"dateFrom": start.isoformat(), "dateTo": end.isoformat()}
            if key == "daily" and campaign_ids:
                params = [
                    ("dateFrom", start.isoformat()),
                    ("dateTo", end.isoformat()),
                ] + [("campaigns", campaign_id) for campaign_id in campaign_ids]
            payload, status = fetch_json(session, path, params)
            if status == 401:
                try:
                    auth = requests.post(
                        BASE + "/api/client/token",
                        json={
                            "client_id": client_id,
                            "client_secret": secret,
                            "grant_type": "client_credentials",
                        },
                        timeout=30,
                    )
                    token = auth.json().get("access_token") if auth.status_code < 400 else None
                    if token:
                        session.headers["Authorization"] = "Bearer " + token
                        payload, status = fetch_json(session, path, params)
                except Exception:
                    status = 0
            if payload is None:
                bundle["errors"].append({
                    "stage": key,
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "status": status,
                })
            else:
                bundle[key].append({
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "response": payload,
                })
            time.sleep(0.2)
    return bundle


def emit_encrypted(bundle):
    encoded = encrypt_bundle(bundle)
    chunks = [encoded[i:i + 3500] for i in range(0, len(encoded), 3500)]
    print("OZON_PAYLOAD_BEGIN")
    for index, chunk in enumerate(chunks, start=1):
        print(f"OZON_CHUNK {index:04d}/{len(chunks):04d} {chunk}")
    print("OZON_PAYLOAD_END")


def perf_rows(bundle, key):
    out = []
    for window in bundle.get(key) or []:
        response = window.get("response") or {}
        rows = response.get("rows") or []
        if not isinstance(rows, list):
            raise RuntimeError("OZON_PERF_ROWS_INVALID:" + key)
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError("OZON_PERF_ROW_INVALID:" + key)
            item = dict(row)
            if key == "campaign_product":
                item["from"] = str(window.get("from") or "")
                item["to"] = str(window.get("to") or "")
            out.append(item)
    return out


def post_publish(payload):
    deployment = os.getenv("WEBAPP_DEPLOYMENT_ID", "").strip()
    secret = os.getenv("OZON_PUBLISH_WEBHOOK_SECRET", "").strip()
    if not deployment or not secret:
        raise RuntimeError("OZON_PUBLISH_BRIDGE_NOT_CONFIGURED")
    print("::add-mask::" + secret)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    url = "https://script.google.com/macros/s/" + deployment + "/exec"
    last = ""
    for attempt in range(1, 6):
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=420) as response:
                result = json.loads(response.read().decode("utf-8"))
            if (
                isinstance(result, dict)
                and result.get("ok") is True
                and result.get("action") == "publish_ozon_performance_daily"
                and result.get("readback") is True
                and result.get("helper_readback") is True
                and result.get("axes_readback") is True
                and result.get("chart_ranges_readback") is True
                and result.get("rnp_note_readback") is True
            ):
                return result
            safe_result = {
                "ok": result.get("ok") if isinstance(result, dict) else None,
                "action": result.get("action") if isinstance(result, dict) else None,
                "error": result.get("error") if isinstance(result, dict) else None,
                "message": result.get("message") if isinstance(result, dict) else None,
                "result_keys": sorted((result.get("result") or {}).keys()) if isinstance(result, dict) and isinstance(result.get("result"), dict) else [],
                "top_level_keys": sorted(result.keys()) if isinstance(result, dict) else [],
                "response_type": type(result).__name__,
            }
            print("OZON_PUBLISH_RESPONSE=" + json.dumps(safe_result, ensure_ascii=False, separators=(",", ":")), flush=True)
            raise RuntimeError("OZON_PUBLISH_INVALID_READBACK_RESPONSE:" + str(safe_result.get("error") or safe_result.get("message") or safe_result.get("top_level_keys")))
        except urllib.error.HTTPError as exc:
            last = "HTTP_" + str(exc.code)
            if exc.code not in {408, 425, 429, 500, 502, 503, 504} or attempt == 5:
                raise RuntimeError("OZON_PUBLISH_FAILED:" + last)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, json.JSONDecodeError) as exc:
            last = type(exc).__name__
            if attempt == 5:
                raise RuntimeError("OZON_PUBLISH_FAILED:" + last)
        delay = min(10 * (2 ** (attempt - 1)), 120)
        print(f"OZON_PUBLISH_RETRY=store={payload.get('store')};attempt={attempt+1}/5;after={last};wait={delay}s", flush=True)
        time.sleep(delay)
    raise RuntimeError("OZON_PUBLISH_RETRY_EXHAUSTED:" + last)


def main():
    store = os.getenv("OZON_STORE", "UNKNOWN")
    client_id = os.getenv("OZON_PERF_CLIENT_ID", "")
    secret = os.getenv("OZON_PERF_CLIENT_SECRET", "")
    last_date = dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=3))).date() - dt.timedelta(days=1)
    bundle = collect_store(store, client_id, secret, last_date)
    for group_name in ("daily", "campaign_product"):
        windows_data = bundle.get(group_name) or []
        rows = [
            row
            for window in windows_data
            for row in ((window.get("response") or {}).get("rows") or [])
            if isinstance(row, dict)
        ]
        dates = [str(row.get("date")) for row in rows if row.get("date")]
        keys = sorted({str(key) for row in rows[:50] for key in row.keys()})
        distinct_keys = set()
        for row in rows:
            if group_name == "daily":
                distinct_keys.add((str(row.get("date") or ""), str(row.get("id") or "")))
            else:
                distinct_keys.add((str(row.get("id") or ""),))
        print(
            "OZON_PERF_SHAPE="
            + json.dumps(
                {
                    "store": store,
                    "group": group_name,
                    "windows": len(windows_data),
                    "rows": len(rows),
                    "date_min": min(dates) if dates else None,
                    "date_max": max(dates) if dates else None,
                    "distinct_days": len(set(dates)),
                    "distinct_keys": len(distinct_keys),
                    "sample_field_names": keys,
                    "errors": [
                        {
                            "stage": item.get("stage"),
                            "status": item.get("status"),
                            "from": item.get("from"),
                            "to": item.get("to"),
                        }
                        for item in bundle.get("errors", [])
                        if item.get("stage") == group_name
                    ],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            flush=True,
        )
    if bundle.get("errors"):
        print("OZON_PERF_PUBLISH=skipped;reason=incomplete_source_response", flush=True)
        return 4

    daily_rows = perf_rows(bundle, "daily")
    period_rows = perf_rows(bundle, "campaign_product")
    if not daily_rows or not period_rows:
        raise RuntimeError("OZON_PERF_EMPTY_EXPORT_PRESERVE")
    dates = sorted({str(row.get("date") or "") for row in daily_rows})
    if not dates or any(not d for d in dates):
        raise RuntimeError("OZON_PERF_DATE_MISSING")
    parsed_dates = [dt.date.fromisoformat(d) for d in dates]
    expected = []
    cursor = parsed_dates[0]
    while cursor <= parsed_dates[-1]:
        expected.append(cursor.isoformat())
        cursor += dt.timedelta(days=1)
    if dates != expected:
        raise RuntimeError("OZON_PERF_DAILY_DATE_GAPS:" + str(len(dates)) + ":" + str(len(expected)))

    daily_keys = [(str(row.get("date") or ""), str(row.get("id") or "")) for row in daily_rows]
    if len(set(daily_keys)) != len(daily_keys):
        raise RuntimeError("OZON_PERF_DAILY_DUPLICATE_KEYS")
    period_keys = [
        (str(row.get("from") or ""), str(row.get("to") or ""), str(row.get("id") or ""))
        for row in period_rows
    ]
    if len(set(period_keys)) != len(period_keys):
        raise RuntimeError("OZON_PERF_PERIOD_DUPLICATE_KEYS")

    payload = {
        "token": os.getenv("OZON_PUBLISH_WEBHOOK_SECRET", ""),
        "action": "publish_ozon_performance_daily",
        "store": store,
        "source": "ozon_performance_api",
        "complete": True,
        "coverage_from": dates[0],
        "coverage_to": dates[-1],
        "daily_rows": daily_rows,
        "period_rows": period_rows,
    }
    result = post_publish(payload)
    print(
        "OZON_PERF_PUBLISH=success;schema=2"
        + ";store=" + str(result.get("store"))
        + ";coverage=" + str(result.get("coverage_from")) + ".." + str(result.get("coverage_to"))
        + ";daily_source=" + str(result.get("source_daily_rows"))
        + ";daily_added=" + str(result.get("daily_added"))
        + ";daily_changed=" + str(result.get("daily_changed"))
        + ";period_source=" + str(result.get("source_period_rows"))
        + ";period_added=" + str(result.get("period_added"))
        + ";period_changed=" + str(result.get("period_changed"))
        + ";archived=" + str(result.get("archived_rows"))
        + ";readback=" + str(result.get("readback"))
        + ";helpers=" + str(result.get("helper_readback"))
        + ";axes=" + str(result.get("axes_readback"))
        + ";chart_ranges_updated=" + str(result.get("chart_ranges_updated"))
        + ";chart_ranges_readback=" + str(result.get("chart_ranges_readback"))
        + ";rnp_note_readback=" + str(result.get("rnp_note_readback"))
        + ";charts=" + str(result.get("charts"))
        + ";daily_sha256=" + str(result.get("daily_sha256"))
        + ";period_sha256=" + str(result.get("period_sha256")),
        flush=True,
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
