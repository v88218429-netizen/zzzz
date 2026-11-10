#!/usr/bin/env python3
"""GitHub-hosted WB FBS recovery for Sanych.

Reads WB Marketplace API; writes only via the authenticated GitHub Google adapter.
No credentials or individual order records are printed or committed.
"""
# SANYCH FBS reconciliation; historical windows are supplied explicitly.

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
MAX_RECOVERY_DAYS = 90
LOOKBACK_DAYS = max(1, min(MAX_RECOVERY_DAYS, int(os.environ.get("FBS_LOOKBACK_DAYS", "7"))))
BATCH_SIZE = max(1, min(100, int(os.environ.get("FBS_BATCH_SIZE", "5"))))  # Keep Apps Script work bounded per gateway call
# The Sanych recovery runs independently; per-client Apps Script leases serialize its writes. Start with the current date segment.
TIMEOUT = 120


def request(url: str, *, token: str | None = None, body: dict | None = None, timeout: int = TIMEOUT, retries: int = 5) -> dict:
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = token
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=encoded, headers=headers, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                # A completed/idempotent bridge write may lose its JSON reply.
                if attempt == retries:
                    raise RuntimeError("INVALID_JSON_RESPONSE_RETRY_EXHAUSTED") from None
                delay = min(60, 10 * attempt)
        except urllib.error.HTTPError as ex:
            if ex.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise RuntimeError(f"HTTP_{ex.code}") from None
            retry_after = ex.headers.get("Retry-After", "0")
            try:
                delay = min(15, max(2 * attempt, float(retry_after)))
            except ValueError:
                delay = 2 * attempt
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == retries:
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
    if end_dt.date() == now.date() and end_dt > now + dt.timedelta(minutes=5):
        end_dt = now
    if end_dt < start_dt or (end_dt.date() - start_dt.date()).days + 1 > MAX_RECOVERY_DAYS or end_dt > now + dt.timedelta(minutes=5):
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
    # A bounded historical run may explicitly include live open orders when
    # repairing a freshness gap. The IDs are upserted, so overlap is safe.
    # The targeted mainggg window uses this to close recent WB freshness gaps.
    include_live = window is None or os.getenv("FBS_INCLUDE_LIVE", "").strip().lower() in {"1", "true", "yes"}
    if include_live:
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
        for oid in subset:
            o = found[oid]
            st = status.get(oid) or {}
            seller = str(st.get("supplierStatus") or st.get("supplier_status") or "").strip()
            wb = str(st.get("wbStatus") or st.get("wb_status") or "").strip()
            if not seller or not wb:
                raise RuntimeError("WB_STATUS_INCOMPLETE: missing_status_count_at_least_1")
            out.append({
                "id": oid, "createdAt": o.get("createdAt") or "",
                "supplierStatus": seller, "wbStatus": wb,
                "article": str(o.get("article") or ""),
                "nmId": o.get("nmId") or "",
                "priceKopecks": o.get("convertedPrice", o.get("convertedFinalPrice",
                                     o.get("finalPrice", o.get("price", 0)))),
                "supplyId": st.get("supplyId") or o.get("supplyId") or "",
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
    for attempt in range(1, 9):
        try:
            data = request(BRIDGE_URL, body={"token": BRIDGE_KEY, "action": "bridge_ping"})
            if data.get("ok") is True and data.get("action") == "bridge_ping":
                print("FBS_AUTHENTICATED_BRIDGE_READY=yes", flush=True)
                return
        except RuntimeError:
            pass
        time.sleep(10)
    raise RuntimeError("FBS_BRIDGE_NOT_DEPLOYED")


def ingest(client: str, orders: list[dict]) -> dict:
    totals = {"received": 0, "inserted": 0, "existing": 0, "changed": 0, "events": 0}
    attempts = 8  # Fast lock-busy responses get a bounded retry window
    main_sheet_last_row = int(orders[0].get("_fbsSheetLastRow", 0)) if orders else 0
    alternate_sheet_last_row = int(orders[0].get("_fbsAltSheetLastRow", 0)) if orders else 0
    if orders and (main_sheet_last_row < 1 or (client == "SANYCH" and alternate_sheet_last_row < 1)):
        raise RuntimeError(client + ":FBS_APPEND_CURSOR_MISSING")
    for order in orders:
        if int(order.get("_fbsSheetLastRow", 0)) != main_sheet_last_row:
            raise RuntimeError(client + ":FBS_APPEND_CURSOR_MISMATCH")
        if client == "SANYCH" and int(order.get("_fbsAltSheetLastRow", 0)) != alternate_sheet_last_row:
            raise RuntimeError(client + ":FBS_ALTERNATE_APPEND_CURSOR_MISMATCH")
    for p in range(0, len(orders), BATCH_SIZE):
        batch = []
        for original in orders[p:p+BATCH_SIZE]:
            item = dict(original)
            item["_fbsSheetLastRow"] = main_sheet_last_row
            if client == "SANYCH":
                item["_fbsAltSheetLastRow"] = alternate_sheet_last_row
            batch.append(item)
        payload = {"token": BRIDGE_KEY, "action": "fbs_ingest_batch",
                   "client": client, "orders": batch}
        network_failures = 0
        for attempt in range(1, attempts + 1):
            try:
                obj = request(BRIDGE_URL, body=payload, timeout=355, retries=1)
            except RuntimeError as ex:
                transient = str(ex) in {
                    "NETWORK_RETRY_EXHAUSTED", "HTTP_429", "HTTP_500",
                    "HTTP_502", "HTTP_503", "HTTP_504",
                    "INVALID_JSON_RESPONSE_RETRY_EXHAUSTED"
                }
                network_failures += 1
                if not transient or network_failures >= 2 or attempt == attempts:
                    raise RuntimeError("FBS_INGEST_TRANSPORT_FAILED:" + str(ex)) from None
                retry_wait_seconds = 430
                print("FBS_INGEST_RETRY=" + str(attempt) + ":" + str(ex) +
                      ":SAME_IDEMPOTENT_BATCH:WAIT_SECONDS=" + str(retry_wait_seconds), flush=True)
                time.sleep(retry_wait_seconds)
                continue

            result = obj.get("result") or {}
            if obj.get("ok") and obj.get("action") == "fbs_ingest_batch" and result.get("ok"):
                break

            details = str(obj.get("error") or result.get("error") or "")
            retryable = result.get("retryable") or any(
                marker in details for marker in (
                    "GITHUB_FBS_LOCK_BUSY", "Service invoked too many times",
                    "Service Spreadsheets failed", "Exceeded maximum execution",
                    "Internal error", "backendError", "HTTP_429", "HTTP_500",
                    "HTTP_502", "HTTP_503", "HTTP_504", "Timeout:", "timed out",
                    "FBS_INSERT_WRITE_TIMEOUT", "FBS_INSERT_WRITE_EXCEPTION"
                )
            )
            if not retryable or attempt == attempts:
                candidates = [
                    "FBS_BATCH_SIZE_INVALID", "FBS_ROW_COUNT_UNEXPECTED",
                    "FBS_DATE_OUTSIDE_RECOVERY_WINDOW", "FBS_ID_INVALID",
                    "FBS_MISSING_CONFIRMED_STATUS", "FBS_DATE_INVALID",
                    "FBS_COLUMN_SCHEMA_MISMATCH", "FBS_INSERT_READBACK_FAILED",
                    "FBS_INSERT_WRITE_TIMEOUT", "FBS_INSERT_WRITE_EXCEPTION",
                    "FBS_BOOK_SCHEMA_MISSING", "FBS_CLIENT_NOT_ALLOWED",
                    "GITHUB_FBS_LOCK_BUSY", "Service Spreadsheets failed",
                    "You do not have permission", "Exceeded maximum execution",
                    "Service invoked too many times", "Exception:", "TypeError:"
                ]
                reason = next((tag.replace(" ", "_") for tag in candidates if tag in details),
                              "UNCLASSIFIED_SCRIPT_REJECTION")
                safe_details = details
                for secret in (BRIDGE_KEY, os.environ.get("WB_API_TOKEN_AP", "")):
                    if secret:
                        safe_details = safe_details.replace(secret, "[redacted]")
                safe_details = re.sub(
                    r"(?i)(authorization|bearer|token|secret|api[_-]?key)(\s*[=:]\s*|\s+)[^\s,;}{]+",
                    r"\1\2[redacted]", safe_details
                )
                safe_details = re.sub(r"(?<!\d)\d{8,}(?!\d)", "[id]", safe_details)
                safe_details = re.sub(r"\s+", " ", safe_details).strip()[:500]
                print("FBS_BRIDGE_REJECT_CLASS=" + reason, flush=True)
                print("FBS_BRIDGE_REJECT_DETAIL=" + (safe_details or "empty"), flush=True)
                raise RuntimeError("INGEST_REJECTED:" + reason)
            time.sleep(min(60, 10 * attempt))

        for field in totals:
            totals[field] += int(result.get(field, 0))
        inserted_now = int(result.get("inserted", 0))
        if inserted_now < 0:
            raise RuntimeError(client + ":FBS_INSERTED_COUNT_INVALID")
        main_sheet_last_row += inserted_now
        if client == "SANYCH":
            alternate_copy = result.get("alternateCopy")
            if not isinstance(alternate_copy, dict) or "inserted" not in alternate_copy:
                raise RuntimeError("SANYCH:FBS_ALTERNATE_INSERT_COUNT_MISSING")
            alternate_inserted_now = int(alternate_copy.get("inserted", 0))
            if alternate_inserted_now < 0:
                raise RuntimeError("SANYCH:FBS_ALTERNATE_INSERTED_COUNT_INVALID")
            alternate_sheet_last_row += alternate_inserted_now
        print("FBS_APPEND_CURSOR=" + json.dumps({"store":client,"batch":p//BATCH_SIZE+1,"main_last_row":main_sheet_last_row,"alternate_last_row":alternate_sheet_last_row}, ensure_ascii=False), flush=True)
        print(json.dumps({"store":client, "batch":p//BATCH_SIZE+1,
              "received":result.get("received"),"inserted":result.get("inserted"),
              "changed":result.get("changed")}, ensure_ascii=False), flush=True)
    return totals




def read_current_states(client: str, orders: list[dict]) -> tuple[dict[str, dict[str, str]], dict[str, object]]:
    states: dict[str, dict[str, str]] = {}
    row_map: dict[str, int] = {}
    alt_row_map: dict[str, int] = {}
    sheet_last_row: int | None = None
    alt_sheet_last_row: int | None = None
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
                read_started = time.monotonic()
                reply = request(
                    BRIDGE_URL,
                    body={"token": BRIDGE_KEY, "action": "fbs_status_read",
                          "client": client, "orderIds": batch_ids},
                    timeout=120,
                    retries=1,
                )
            except RuntimeError as ex:
                last_detail = str(ex)
                transient = any(marker.lower() in last_detail.lower() for marker in transient_markers)
                if not transient or attempt == 4:
                    detail = safe_bridge_detail(last_detail) if "safe_bridge_detail" in globals() else re.sub(r"\s+", " ", last_detail)[:240]
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
                batch_rows = result.get("rowMap")
                try:
                    batch_last_row = int(result.get("sheetLastRow", 0))
                except (TypeError, ValueError):
                    batch_last_row = 0
                if (not isinstance(batch_states, dict) or not isinstance(batch_rows, dict)
                        or batch_last_row < 1):
                    raise RuntimeError(client + ":FBS_STATUS_READ_METADATA_INVALID")
                if sheet_last_row is not None and sheet_last_row != batch_last_row:
                    raise RuntimeError(client + ":FBS_STATUS_READ_LAST_ROW_CHANGED")
                sheet_last_row = batch_last_row
                allowed = set(batch_ids)
                for oid, pair in batch_states.items():
                    if oid not in allowed or not isinstance(pair, dict):
                        raise RuntimeError(client + ":FBS_STATUS_READ_INVALID")
                    seller, wb = pair.get("supplierStatus"), pair.get("wbStatus")
                    if not isinstance(seller, str) or not isinstance(wb, str):
                        raise RuntimeError(client + ":FBS_STATUS_READ_INVALID")
                    states[oid] = {"supplierStatus": seller, "wbStatus": wb}
                for oid, row_value in batch_rows.items():
                    try:
                        row = int(row_value)
                    except (TypeError, ValueError):
                        raise RuntimeError(client + ":FBS_STATUS_READ_ROW_HINT_INVALID") from None
                    if oid not in allowed or row < 2 or row > batch_last_row:
                        raise RuntimeError(client + ":FBS_STATUS_READ_ROW_HINT_INVALID")
                    row_map[oid] = row
                try:
                    batch_alt_last = int(result.get("alternateSheetLastRow", 0))
                except (TypeError, ValueError):
                    batch_alt_last = 0
                batch_alt_rows = result.get("alternateRowMap")
                if not isinstance(batch_alt_rows, dict) or batch_alt_last < 1:
                    raise RuntimeError(client + ":FBS_STATUS_READ_ALTERNATE_METADATA_INVALID")
                if alt_sheet_last_row is not None and alt_sheet_last_row != batch_alt_last:
                    raise RuntimeError(client + ":FBS_STATUS_READ_ALTERNATE_LAST_ROW_CHANGED")
                alt_sheet_last_row = batch_alt_last
                for oid, row_value in batch_alt_rows.items():
                    try:
                        row = int(row_value)
                    except (TypeError, ValueError):
                        raise RuntimeError(client + ":FBS_STATUS_READ_ALTERNATE_ROW_HINT_INVALID") from None
                    if oid not in allowed or row < 2 or row > batch_alt_last:
                        raise RuntimeError(client + ":FBS_STATUS_READ_ALTERNATE_ROW_HINT_INVALID")
                    alt_row_map[oid] = row

                print("FBS_STATUS_READ_DONE="+json.dumps({"store":client,"chunk":offset//1000+1,
                      "requested":len(batch_ids),"found":len(batch_states),
                      "elapsed_sec":round(time.monotonic()-read_started,2)}),flush=True)
                break
            last_detail = str(reply.get("error") or result.get("error") or "FBS_STATUS_READ_INVALID_RESPONSE")
            transient = bool(result.get("retryable")) or any(
                marker.lower() in last_detail.lower() for marker in transient_markers
            )
            if not transient or attempt == 4:
                detail = re.sub(r"\s+", " ", last_detail)[:240]
                print(client + "_FBS_STATUS_READ_REJECT=" + detail, flush=True)
                raise RuntimeError(client + ":FBS_STATUS_READ_REJECTED") from None
            wait_seconds = min(30, 10 * attempt)
            print(client + "_FBS_STATUS_READ_RETRY=" + str(attempt) +
                  ":SAME_READ_ONLY_REQUEST:WAIT_SECONDS=" + str(wait_seconds), flush=True)
            time.sleep(wait_seconds)
        else:
            raise RuntimeError(client + ":FBS_STATUS_READ_REJECTED")
    if orders and sheet_last_row is None:
        raise RuntimeError(client + ":FBS_STATUS_READ_METADATA_MISSING")
    metadata: dict[str, object] = {
        "rowMap": row_map,
        "sheetLastRow": sheet_last_row or 1,
        "alternateRowMap": alt_row_map,
        "alternateSheetLastRow": alt_sheet_last_row or 1,
    }
    return states, metadata


def select_changed_orders(client: str, orders: list[dict]) -> tuple[list[dict], int]:
    filter_started=time.monotonic()
    states, metadata = read_current_states(client, orders)
    row_map = metadata["rowMap"]
    changed = []
    for order in orders:
        oid = str(order.get("id") or "")
        current = states.get(oid)
        if (current is None
                or current["supplierStatus"] != str(order.get("supplierStatus") or "").strip()
                or current["wbStatus"] != str(order.get("wbStatus") or "").strip()):
            enriched = dict(order)
            enriched["_fbsRowHint"] = int(row_map.get(oid, 0))
            enriched["_fbsSheetLastRow"] = int(metadata["sheetLastRow"])
            enriched["_fbsAltRowHint"] = int(metadata["alternateRowMap"].get(oid, 0))
            enriched["_fbsAltSheetLastRow"] = int(metadata["alternateSheetLastRow"])
            changed.append(enriched)
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
        "SANYCH": "WB_API_TOKEN_AP",
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
