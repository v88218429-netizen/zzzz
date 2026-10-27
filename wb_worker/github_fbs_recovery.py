#!/usr/bin/env python3
"""GitHub-hosted WB FBS recovery for AIR and Hozyayushka.

Reads WB Marketplace API; writes only via the authenticated GitHub Google adapter.
No credentials or individual order records are printed or committed.
"""
# [FBS-90D-ONCE] One-time 90-day WB reconciliation; end at the actual run time (UTC).

from __future__ import annotations
import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.request

BASE = "https://marketplace-api.wildberries.ru"
BRIDGE_URL = os.environ["GOOGLE_BRIDGE_URL"]
BRIDGE_KEY = os.environ["GOOGLE_BRIDGE_KEY"]
LOOKBACK_DAYS = max(1, min(90, int(os.environ.get("FBS_LOOKBACK_DAYS", "7"))))
BATCH_SIZE = 100  # Keep each idempotent write bounded while the adapter coalesces neighboring rows
INGEST_ATTEMPTS = 8  # Allow temporary Apps Script lock holders to finish
BRIDGE_INGEST_TIMEOUT = 390  # Let Apps Script finish or hit its six-minute execution ceiling before retrying
TIMEOUT = 120


def safe_bridge_detail(value: object) -> str:
    detail = str(value or "")
    detail = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [redacted]", detail)
    detail = re.sub(r"\b[A-Za-z0-9_-]{32,}\b", "[opaque-id]", detail)
    detail = re.sub(r"\b\d{6,20}\b", "[order-id]", detail)
    return re.sub(r"\s+", " ", detail).strip()[:240]


def request(url: str, *, token: str | None = None, body: dict | None = None,
            max_attempts: int = 8, timeout: int | None = None) -> dict:
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = token
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(url, data=encoded, headers=headers, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as response:
                raw = response.read().decode("utf-8")
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Apps Script can return an empty/HTML response after a timed-out
                # execution even when the idempotent sheet write may have committed.
                if attempt == max_attempts:
                    raise RuntimeError("INVALID_JSON_RESPONSE_RETRY_EXHAUSTED") from None
                delay = min(60, 10 * attempt)
        except urllib.error.HTTPError as ex:
            if ex.code not in (429, 500, 502, 503, 504) or attempt == max_attempts:
                raise RuntimeError(f"HTTP_{ex.code}") from None
            retry_after = ex.headers.get("Retry-After", "0")
            try:
                delay = min(15, max(2 * attempt, float(retry_after)))
            except ValueError:
                delay = 2 * attempt
        except (urllib.error.URLError, TimeoutError, OSError):
            # urllib/http.client can raise RemoteDisconnected directly (an OSError),
            # instead of wrapping it in URLError. Treat it as a lost response.
            if attempt == max_attempts:
                raise RuntimeError("NETWORK_RETRY_EXHAUSTED") from None
            delay = 2 * attempt
        time.sleep(delay)
    raise RuntimeError("RETRY_EXHAUSTED")


def endpoint(token: str, path: str, body: dict | None = None) -> dict:
    return request(BASE + path, token=token, body=body)


def requested_window() -> tuple[int, int] | None:
    start_raw = os.environ.get("FBS_START_DATE", "").strip()
    end_raw = os.environ.get("FBS_END_DATE", "").strip()
    if not start_raw and not end_raw and os.environ.get("GITHUB_EVENT_NAME") == "push":
        event_path = os.environ.get("GITHUB_EVENT_PATH", "")
        if event_path:
            try:
                with open(event_path, "r", encoding="utf-8") as event_file:
                    message = str((json.load(event_file).get("head_commit") or {}).get("message") or "")
                match = re.search(r"\[FBS-RANGE:(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})\]", message)
                if match:
                    start_raw, end_raw = match.group(1), match.group(2)
            except (OSError, ValueError, TypeError):
                pass
    if not start_raw and not end_raw:
        return None
    if not start_raw or not end_raw:
        raise RuntimeError("FBS_DATE_RANGE_INCOMPLETE")
    try:
        start_dt = dt.datetime.strptime(start_raw, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        end_dt = dt.datetime.strptime(end_raw, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=1) - dt.timedelta(seconds=1)
    except ValueError:
        raise RuntimeError("FBS_DATE_RANGE_INVALID") from None
    now = dt.datetime.now(dt.timezone.utc)
    if end_dt < start_dt or (end_dt.date() - start_dt.date()).days + 1 > 90 or end_dt > now + dt.timedelta(minutes=5):
        raise RuntimeError("FBS_DATE_RANGE_OUT_OF_BOUNDS")
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def collect(token: str) -> list[dict]:
    window = requested_window()
    if window is None:
        now = dt.datetime.now(dt.timezone.utc)
        end = int(now.timestamp())
        start = int((now - dt.timedelta(days=LOOKBACK_DAYS)).timestamp())
    else:
        start, end = window
    found: dict[str, dict] = {}
    window_end = end
    while window_end > start:
        window_start = max(start, window_end - 30 * 86400)
        next_cursor = "0"
        cursors: set[str] = set()
        for _ in range(30):
            if next_cursor in cursors:
                raise RuntimeError("REPEATED_CURSOR")
            cursors.add(next_cursor)
            page = endpoint(
                token,
                f"/api/v3/orders?limit=1000&next={next_cursor}&dateFrom={window_start}&dateTo={window_end}",
            )
            for order in page.get("orders") or []:
                oid = str(order.get("id") or order.get("orderId") or "")
                if oid.isdecimal():
                    found[oid] = order
            nxt = str(page.get("next") or "0")
            if not (page.get("orders") or []) or nxt == "0":
                break
            next_cursor = nxt
            time.sleep(0.3)
        else:
            raise RuntimeError("ORDERS_PAGE_LIMIT_EXCEEDED")
        if len(found) > 30000:
            raise RuntimeError("ORDERS_ROW_LIMIT_EXCEEDED")
        window_end = window_start - 1
    # /new includes orders not yet present in the paginated history.
    for order in endpoint(token, "/api/v3/orders/new").get("orders") or []:
        oid = str(order.get("id") or order.get("orderId") or "")
        if oid.isdecimal():
            found[oid] = order
    if len(found) > 30000:
        raise RuntimeError("ORDERS_ROW_LIMIT_EXCEEDED")
    out = []
    ids = list(found)
    for pos in range(0, len(ids), 100):
        subset = ids[pos:pos + 100]
        reply = endpoint(token, "/api/v3/orders/status", {"orders": [int(x) for x in subset]})
        status = {
            str(x.get("id") or x.get("orderId") or ""): x
            for x in (reply.get("orders") or [])
        }
        def status_pair(oid: str) -> tuple[str, str]:
            row = status.get(oid) or {}
            return (
                str(row.get("supplierStatus") or row.get("supplier_status") or "").strip(),
                str(row.get("wbStatus") or row.get("wb_status") or "").strip(),
            )
        missing = [oid for oid in subset if not all(status_pair(oid))]
        # A status snapshot can briefly omit a just-created order. Re-read only
        # those IDs; never invent a status or let a partial response halt the batch.
        for retry in range(1, 4):
            if not missing:
                break
            time.sleep(2 * retry)
            check = endpoint(token, "/api/v3/orders/status", {"orders": [int(x) for x in missing]})
            status.update({
                str(x.get("id") or x.get("orderId") or ""): x
                for x in (check.get("orders") or [])
            })
            missing = [oid for oid in missing if not all(status_pair(oid))]
        if missing:
            raise RuntimeError("WB_STATUS_INCOMPLETE: missing_status_count=" + str(len(missing)))
        for oid in subset:
            o = found[oid]
            seller, wb = status_pair(oid)
            out.append({
                "id": oid, "createdAt": o.get("createdAt") or "",
                "supplierStatus": seller, "wbStatus": wb,
                "article": str(o.get("article") or ""),
                "nmId": o.get("nmId") or "",
                "priceKopecks": o.get("convertedPrice", o.get("convertedFinalPrice",
                                     o.get("finalPrice", o.get("price", 0)))),
                "supplyId": (status.get(oid) or {}).get("supplyId") or o.get("supplyId") or "",
                "warehouseId": o.get("warehouseId") or "",
                "officeId": o.get("officeId") or o.get("destinationOfficeId") or "",
                "deliveryType": o.get("deliveryType") or "",
                "cargoType": o.get("cargoType") or "",
                "orderUid": o.get("orderUid") or "",
                "rid": o.get("rid") or "",
                "comment": o.get("comment") or "",
                "barcode": (o.get("skus") or [""])[0],
                "chrtId": o.get("chrtId") or o.get("chrtID") or "",
            })
    return out

def wait_bridge() -> None:
    last_error = "BRIDGE_RESPONSE_INVALID"
    for attempt in range(1, 7):
        try:
            data = request(BRIDGE_URL, body={"token": BRIDGE_KEY, "action": "fbs_targets_health"}, max_attempts=1, timeout=120)
            if data.get("ok") is True and data.get("action") == "fbs_targets_health":
                print("FBS_AUTHENTICATED_BRIDGE_READY=yes", flush=True)
                return
            last_error = "BRIDGE_RESPONSE_INVALID"
        except RuntimeError as ex:
            last_error = str(ex)
        if attempt < 6:
            print("FBS_BRIDGE_PROBE_RETRY=" + str(attempt) + ":" + last_error, flush=True)
            time.sleep(min(60, 15 * attempt))
    raise RuntimeError("FBS_BRIDGE_NOT_READY:" + last_error)


def ingest(client: str, orders: list[dict]) -> dict:
    totals = {"received": 0, "inserted": 0, "existing": 0, "changed": 0, "events": 0}
    for p in range(0, len(orders), BATCH_SIZE):
        batch = orders[p:p+BATCH_SIZE]
        print("FBS_INGEST_BATCH_START="+json.dumps({"store":client,"batch":p//BATCH_SIZE+1,"orders":len(batch)}), flush=True)
        payload = {"token": BRIDGE_KEY, "action": "fbs_ingest_batch",
                   "client": client, "orders": batch}
        for attempt in range(1, INGEST_ATTEMPTS + 1):
            try:
                obj = request(BRIDGE_URL, body=payload, max_attempts=1, timeout=BRIDGE_INGEST_TIMEOUT)
                result = obj.get("result") or {}
                if obj.get("ok") and obj.get("action") == "fbs_ingest_batch" and result.get("ok"):
                    break
                details = str(obj.get("error") or result.get("error") or "")
                retryable = result.get("retryable") or any(
                    marker in details for marker in (
                        "GITHUB_FBS_LOCK_BUSY", "Service invoked too many times",
                        "Service Spreadsheets failed", "Exceeded maximum execution",
                        "Internal error", "backendError"
                    )
                )
                if not retryable and details:
                    print("FBS_BRIDGE_ERROR_DETAIL=" + safe_bridge_detail(details), flush=True)
                if not retryable:
                    candidates = [
                        "FBS_BATCH_SIZE_INVALID", "FBS_ROW_COUNT_UNEXPECTED",
                        "FBS_DATE_OUTSIDE_RECOVERY_WINDOW", "FBS_ID_INVALID",
                        "FBS_MISSING_CONFIRMED_STATUS", "FBS_DATE_INVALID",
                        "FBS_COLUMN_SCHEMA_MISMATCH", "FBS_INSERT_READBACK_FAILED",
                        "FBS_BOOK_SCHEMA_MISSING", "FBS_CLIENT_NOT_ALLOWED",
                        "GITHUB_FBS_LOCK_BUSY", "Service Spreadsheets failed",
                        "You do not have permission", "Exceeded maximum execution",
                        "Service invoked too many times", "Exception:", "TypeError:"
                    ]
                    reason = next((tag.replace(" ", "_") for tag in candidates if tag in details),
                                  "UNCLASSIFIED_SCRIPT_REJECTION")
                    print("FBS_BRIDGE_REJECT_CLASS=" + reason, flush=True)
                    raise RuntimeError("INGEST_REJECTED:" + reason)
                if attempt == INGEST_ATTEMPTS:
                    print("FBS_BRIDGE_ERROR_DETAIL=" + safe_bridge_detail(details), flush=True)
                    raise RuntimeError("INGEST_REJECTED:" + safe_bridge_detail(details))
            except RuntimeError as ex:
                reason = str(ex)
                if reason in ("NETWORK_RETRY_EXHAUSTED", "INVALID_JSON_RESPONSE_RETRY_EXHAUSTED") or reason.startswith(("HTTP_500", "HTTP_502", "HTTP_503", "HTTP_504")):
                    # Ingest is an upsert keyed by WB order ID. Repeating this exact
                    # batch after a lost response is idempotent and recovers lost replies.
                    if attempt == INGEST_ATTEMPTS:
                        raise RuntimeError("FBS_INGEST_OUTCOME_UNCERTAIN") from None
                    print("FBS_INGEST_RETRY=" + str(attempt) + ":SAME_IDEMPOTENT_BATCH", flush=True)
                    time.sleep(min(60, 15 * attempt))
                    continue
                if attempt == INGEST_ATTEMPTS:
                    raise
                print("FBS_INGEST_RETRY=" + str(attempt) + ":LOCK_OR_RETRYABLE_RESPONSE", flush=True)
            time.sleep(min(120, 15 * attempt))
        for field in totals:
            totals[field] += int(result.get(field, 0))
        print(json.dumps({"store":client, "batch":p//BATCH_SIZE+1,
              "received":result.get("received"),"inserted":result.get("inserted"),
              "changed":result.get("changed")}, ensure_ascii=False), flush=True)
    return totals




def main() -> None:
    if len(BRIDGE_KEY) < 16:
        raise RuntimeError("BRIDGE_AUTH_NOT_CONFIGURED")
    selected = os.environ.get("FBS_CLIENT", "").strip().upper()
    clients = {
        "AIR": "WB_API_TOKEN_AA",
        "HOZYAYUSHKA": "WB_API_TOKEN_YV",
    }
    if selected and selected not in clients:
        raise RuntimeError("FBS_CLIENT_NOT_ALLOWED")
    targets = [(selected, clients[selected])] if selected else list(clients.items())
    wait_bridge()
    summary = {}
    for client, key in targets:
        token = os.environ.get(key, "")
        if len(token) < 30:
            raise RuntimeError(client + ":WB_TOKEN_MISSING")
        orders = collect(token)
        if not orders:
            summary[client] = {"received": 0, "inserted": 0, "existing": 0}
            continue
        counts = ingest(client, orders)
        if counts["received"] != len(orders):
            raise RuntimeError(client + ":ROWS_UNACCOUNTED")
        summary[client] = counts
    print("FBS_GITHUB_SYNC_SUMMARY=" + json.dumps(summary, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
