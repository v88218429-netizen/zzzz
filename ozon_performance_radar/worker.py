import base64
import datetime as dt
import gzip
import json
import os
import sys
import time

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
    for path in STAT_ENDPOINTS:
        key = "daily" if path.endswith("/daily/json") else "campaign_product"
        for start, end in windows(FIRST_DATE, last_date):
            params = {"dateFrom": start.isoformat(), "dateTo": end.isoformat()}
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


def main():
    store = os.getenv("OZON_STORE", "UNKNOWN")
    client_id = os.getenv("OZON_PERF_CLIENT_ID", "")
    secret = os.getenv("OZON_PERF_CLIENT_SECRET", "")
    last_date = dt.datetime.now(dt.timezone.utc).date()
    bundle = collect_store(store, client_id, secret, last_date)
    emit_encrypted(bundle)
    return 0 if not bundle["errors"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
