#!/usr/bin/env python3
"""GitHub-hosted WB FBS recovery for AIR and Hozyayushka.

Reads WB Marketplace API; writes only via the authenticated GitHub Google adapter.
No credentials or individual order records are printed or committed.
"""

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
# AIR needs smaller writes: sparse status changes otherwise exceed Apps Script execution time.
# Keep AIR gateway calls small enough to finish within the Apps Script runtime ceiling.
# New recovery runs replace the stalled legacy matrix; upserts remain safe to resume.
# Batch identity searches on the live gateway are bounded to the IDs in each idempotent batch.
# Recovery matrix includes the current UTC day so recent WB orders are reconciled too.
# Current-day bounds are clamped to now before calling the official WB API.
CLIENT_HINT = os.environ.get("FBS_CLIENT", "").strip().upper()
BATCH_SIZE = int(os.environ.get("FBS_BATCH_SIZE", "10"))
if not 1 <= BATCH_SIZE <= 1000:
    raise RuntimeError("FBS_BATCH_SIZE_INVALID")
INGEST_ATTEMPTS = 8  # Fast lock-busy responses get a bounded retry window
NETWORK_FAILURE_LIMIT = 2  # Retry one ambiguous response only after the seven-minute lease can expire
BRIDGE_INGEST_TIMEOUT = 355  # Leave a small margin below the Apps Script six-minute ceiling
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
    if end_dt.date() > now.date():
        raise RuntimeError("FBS_DATE_RANGE_OUT_OF_BOUNDS")
    if end_dt.date() == now.date():
        end_dt = now
    if end_dt < start_dt or (end_dt.date() - start_dt.date()).days + 1 > 90:
        raise RuntimeError("FBS_DATE_RANGE_OUT_OF_BOUNDS")
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def collect(token: str) -> list[dict]:
    collect_started = time.monotonic()
    window = requested_window()
    if window is None:
        now = dt.datetime.now(dt.timezone.utc)
        end = int(now.timestamp())
        start = int((now - dt.timedelta(days=LOOKBACK_DAYS)).timestamp())
    else:
        start, end = window
    print("FBS_WB_COLLECT_START="+json.dumps({"start_epoch":start,"end_epoch":end,"window_days":max(1,(end-start)//86400+1)}),flush=True)
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
            page_started = time.monotonic()
            page = endpoint(
                token,
                f"/api/v3/orders?limit=1000&next={next_cursor}&dateFrom={window_start}&dateTo={window_end}",
            )
            page_orders = page.get("orders") or []
            print("FBS_WB_ORDERS_PAGE="+json.dumps({"received":len(page_orders),"elapsed_sec":round(time.monotonic()-page_started,2)}),flush=True)
            for order in page_orders:
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
    # Only current refreshes need the live /new feed; replaying it for every
    # historical date repeats the full open-order list and creates needless writes.
    include_live = window is None or (
        dt.datetime.fromtimestamp(window[1], tz=dt.timezone.utc).date()
        >= dt.datetime.now(dt.timezone.utc).date()
    )
    if include_live:
        for order in endpoint(token, "/api/v3/orders/new").get("orders") or []:
            oid = str(order.get("id") or order.get("orderId") or "")
            if oid.isdecimal():
                found[oid] = order
    if len(found) > 30000:
        raise RuntimeError("ORDERS_ROW_LIMIT_EXCEEDED")
    out = []
    ids = list(found)
    status_chunks = (len(ids)+99)//100
    print("FBS_WB_STATUS_ENRICH_START="+json.dumps({"orders":len(ids),"chunks":status_chunks}),flush=True)
    for pos in range(0, len(ids), 100):
        subset = ids[pos:pos + 100]
        status_started = time.monotonic()
        reply = endpoint(token, "/api/v3/orders/status", {"orders": [int(x) for x in subset]})
        print("FBS_WB_STATUS_CHUNK="+json.dumps({"chunk":pos//100+1,"chunks":status_chunks,"requested":len(subset),"returned":len(reply.get("orders") or []),"elapsed_sec":round(time.monotonic()-status_started,2)}),flush=True)
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
    print("FBS_WB_COLLECT_DONE="+json.dumps({"orders":len(out),"elapsed_sec":round(time.monotonic()-collect_started,2)}),flush=True)
    return out

def wait_bridge() -> None:
    last_error = "BRIDGE_RESPONSE_INVALID"
    for attempt in range(1, 7):
        try:
            data = request(BRIDGE_URL, body={"token": BRIDGE_KEY, "action": "bridge_ping"}, max_attempts=1, timeout=120)
            if data.get("ok") is True and data.get("action") == "bridge_ping":
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
        batch_started = time.monotonic()
        print("FBS_INGEST_BATCH_START="+json.dumps({"store":client,"batch":p//BATCH_SIZE+1,"orders":len(batch)}), flush=True)
        payload = {"token": BRIDGE_KEY, "action": "fbs_ingest_batch",
                   "client": client, "orders": batch}
        network_failures = 0
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
                        "Internal error", "backendError", "Timeout:", "timed out",
                        # Fixed-range order-ID upserts are safe to retry after ambiguous responses.
                        "FBS_INSERT_WRITE_TIMEOUT", "FBS_INSERT_WRITE_EXCEPTION",
                        "FBS_INSERT_HTTP_429", "FBS_INSERT_HTTP_500",
                        "FBS_INSERT_HTTP_502", "FBS_INSERT_HTTP_503", "FBS_INSERT_HTTP_504"
                    )
                )
                if not retryable and details:
                    print("FBS_BRIDGE_ERROR_DETAIL=" + safe_bridge_detail(details), flush=True)
                if not retryable:
                    candidates = [
                        "FBS_SHEET_READ_HTTP_403", "FBS_STATE_BATCH_READ_HTTP_403", "PERMISSION_DENIED",
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
                if reason.startswith("INGEST_REJECTED:"):
                    # Deterministic Apps Script rejections are not safe to retry
                    # repeatedly; surface the exact classified cause immediately.
                    raise
                if reason in ("NETWORK_RETRY_EXHAUSTED", "INVALID_JSON_RESPONSE_RETRY_EXHAUSTED") or reason.startswith(("HTTP_500", "HTTP_502", "HTTP_503", "HTTP_504")):
                    # Ingest is an upsert keyed by WB order ID. Repeating this exact
                    # batch after a lost response is idempotent and recovers lost replies.
                    network_failures += 1
                    if attempt == INGEST_ATTEMPTS or network_failures >= NETWORK_FAILURE_LIMIT:
                        raise RuntimeError("FBS_INGEST_OUTCOME_UNCERTAIN_AFTER_IDEMPOTENT_RETRIES") from None
                    failure_match = re.match(r"HTTP_(\d{3})", reason)
                    failure_class = ("HTTP_" + failure_match.group(1)) if failure_match else reason
                    retry_wait_seconds = 430
                    print("FBS_INGEST_RETRY=" + str(attempt) + ":" + failure_class +
                          ":SAME_IDEMPOTENT_BATCH:WAIT_SECONDS=" + str(retry_wait_seconds), flush=True)
                    time.sleep(retry_wait_seconds)
                    continue
                if attempt == INGEST_ATTEMPTS:
                    raise
                print("FBS_INGEST_RETRY=" + str(attempt) + ":LOCK_OR_RETRYABLE_RESPONSE", flush=True)
            # A busy client lease expires after seven minutes; bounded backoff lets
            # an earlier or timed-out Apps Script call release it before giving up.
            time.sleep(min(120, 15 * attempt))
        for field in totals:
            totals[field] += int(result.get(field, 0))
        print(json.dumps({"store":client, "batch":p//BATCH_SIZE+1,
              "received":result.get("received"),"inserted":result.get("inserted"),
              "changed":result.get("changed"),"events":result.get("events"),
              "elapsed_sec":round(time.monotonic()-batch_started,2)}, ensure_ascii=False), flush=True)
    return totals





def read_current_states(client: str, orders: list[dict]) -> dict[str, dict[str, str]]:
    states: dict[str, dict[str, str]] = {}
    ids = [str(order.get("id") or "") for order in orders]
    transient_markers = (
        "NETWORK_RETRY_EXHAUSTED", "INVALID_JSON_RESPONSE_RETRY_EXHAUSTED",
        "HTTP_408", "HTTP_425", "HTTP_429", "HTTP_500", "HTTP_502",
        "HTTP_503", "HTTP_504", "Timeout:", "timed out", "backendError",
        "Internal error", "Service unavailable",
    )
    for offset in range(0, len(ids), 1000):
        batch_ids = ids[offset:offset + 1000]
        last_detail = "FBS_STATUS_READ_INVALID_RESPONSE"
        print("FBS_STATUS_READ_START="+json.dumps({"store":client,"chunk":offset//1000+1,"requested":len(batch_ids)}),flush=True)
        for attempt in range(1, 5):
            try:
                read_started=time.monotonic()
                reply = request(
                    BRIDGE_URL,
                    body={"token": BRIDGE_KEY, "action": "fbs_status_read",
                          "client": client, "orderIds": batch_ids},
                    max_attempts=1,
                    timeout=120,
                )
            except RuntimeError as ex:
                last_detail = str(ex)
                transient = any(marker.lower() in last_detail.lower() for marker in transient_markers)
                if not transient or attempt == 4:
                    detail = safe_bridge_detail(last_detail)
                    print(client + "_FBS_STATUS_READ_REJECT=" + detail, flush=True)
                    raise RuntimeError(client + ":FBS_STATUS_READ_REJECTED") from None
                wait_seconds = min(30, 10 * attempt)
                print(client + "_FBS_STATUS_READ_RETRY=" + str(attempt) +
                      ":SAME_READ_ONLY_REQUEST:WAIT_SECONDS=" + str(wait_seconds), flush=True)
                time.sleep(wait_seconds)
                continue

            result = reply.get("result") or {}
            if (reply.get("ok") is True and reply.get("action") == "fbs_status_read"
                    and result.get("ok") is True
                    and int(result.get("requested", -1)) == len(batch_ids)):
                batch_states = result.get("statuses")
                if not isinstance(batch_states, dict):
                    last_detail = "FBS_STATUS_READ_INVALID"
                    print(client + "_FBS_STATUS_READ_REJECT=" + last_detail, flush=True)
                    raise RuntimeError(client + ":FBS_STATUS_READ_INVALID")
                allowed = set(batch_ids)
                valid = True
                for oid, pair in batch_states.items():
                    if oid not in allowed or not isinstance(pair, dict):
                        valid = False
                        break
                    seller = pair.get("supplierStatus")
                    wb = pair.get("wbStatus")
                    if not isinstance(seller, str) or not isinstance(wb, str):
                        valid = False
                        break
                if not valid:
                    last_detail = "FBS_STATUS_READ_INVALID"
                    print(client + "_FBS_STATUS_READ_REJECT=" + last_detail, flush=True)
                    raise RuntimeError(client + ":FBS_STATUS_READ_INVALID")
                for oid, pair in batch_states.items():
                    states[oid] = {
                        "supplierStatus": pair["supplierStatus"],
                        "wbStatus": pair["wbStatus"],
                    }
                print("FBS_STATUS_READ_DONE="+json.dumps({"store":client,"chunk":offset//1000+1,
                      "requested":len(batch_ids),"found":len(batch_states),
                      "elapsed_sec":round(time.monotonic()-read_started,2)}),flush=True)
                break
            else:
                last_detail = str(reply.get("error") or result.get("error") or (
                    "shape: outer_ok=" + str(reply.get("ok")) +
                    " action=" + str(reply.get("action")) +
                    " result_ok=" + str(result.get("ok")) +
                    " requested=" + str(result.get("requested")) +
                    " expected=" + str(len(batch_ids))
                ))
                transient = bool(result.get("retryable")) or any(
                    marker.lower() in last_detail.lower() for marker in transient_markers
                )
                if not transient or attempt == 4:
                    detail = safe_bridge_detail(last_detail)
                    print(client + "_FBS_STATUS_READ_REJECT=" + detail, flush=True)
                    raise RuntimeError(client + ":FBS_STATUS_READ_REJECTED") from None
                wait_seconds = min(30, 10 * attempt)
                print(client + "_FBS_STATUS_READ_RETRY=" + str(attempt) +
                      ":SAME_READ_ONLY_REQUEST:WAIT_SECONDS=" + str(wait_seconds), flush=True)
                time.sleep(wait_seconds)
        else:
            detail = safe_bridge_detail(last_detail)
            print(client + "_FBS_STATUS_READ_REJECT=" + detail, flush=True)
            raise RuntimeError(client + ":FBS_STATUS_READ_REJECTED")
    return states

def select_changed_orders(client: str, orders: list[dict]) -> tuple[list[dict], int]:
    filter_started=time.monotonic()
    states = read_current_states(client, orders)
    changed = []
    for order in orders:
        oid = str(order.get("id") or "")
        current = states.get(oid)
        if (current is None
                or current["supplierStatus"] != str(order.get("supplierStatus") or "").strip()
                or current["wbStatus"] != str(order.get("wbStatus") or "").strip()):
            changed.append(order)
    unchanged=len(orders)-len(changed)
    print("FBS_CHANGE_FILTER_DONE="+json.dumps({"store":client,"orders":len(orders),
          "changed":len(changed),"unchanged":unchanged,
          "elapsed_sec":round(time.monotonic()-filter_started,2)}),flush=True)
    return changed, unchanged


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
    print("FBS_RECOVERY_PLAN=" + json.dumps({
        "client": selected or "AIR+HOZYAYUSHKA",
        "batch_size": BATCH_SIZE,
        "lookback_days": LOOKBACK_DAYS,
        "start_date": os.environ.get("FBS_START_DATE", ""),
        "end_date": os.environ.get("FBS_END_DATE", ""),
    }, sort_keys=True), flush=True)
    wait_bridge()
    summary = {}
    for client, key in targets:
        token = os.environ.get(key, "")
        if len(token) < 30:
            raise RuntimeError(client + ":WB_TOKEN_MISSING")
        orders = collect(token)
        if not orders:
            summary[client] = {"received": 0, "inserted": 0, "existing": 0, "changed": 0, "events": 0}
            continue
        candidates, unchanged = select_changed_orders(client, orders)
        print(client + "_FBS_UNCHANGED_SKIPPED=" + str(unchanged), flush=True)
        counts = ingest(client, candidates) if candidates else {
            "received": 0, "inserted": 0, "existing": 0, "changed": 0, "events": 0
        }
        if (counts["received"] != len(candidates)
                or counts["inserted"] + counts["existing"] != len(candidates)):
            raise RuntimeError(client + ":ROWS_UNACCOUNTED")
        summary[client] = {
            "received": len(orders),
            "inserted": counts["inserted"],
            "existing": counts["existing"] + unchanged,
            "changed": counts["changed"],
            "events": counts["events"],
        }
    print("FBS_GITHUB_SYNC_SUMMARY=" + json.dumps(summary, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
