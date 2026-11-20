from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .base import BaseAgent
from ..models import AgentResult


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _card_error_rows(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    inner = data.get("data")
    items = inner.get("items") if isinstance(inner, dict) else None
    if not isinstance(items, list):
        return []
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        updated = item.get("updatedAt") or item.get("updated_at")
        errors = item.get("errors") if isinstance(item.get("errors"), dict) else {}
        subjects = item.get("subjects") if isinstance(item.get("subjects"), dict) else {}
        vendor_codes = item.get("vendorCodes") if isinstance(item.get("vendorCodes"), list) else list(errors)
        for vendor in vendor_codes:
            vendor_s = str(vendor or "").strip()
            if not vendor_s:
                continue
            raw_errors = errors.get(vendor_s)
            if isinstance(raw_errors, str):
                raw_errors = [raw_errors]
            if not isinstance(raw_errors, list):
                raw_errors = []
            subject = subjects.get(vendor_s) if isinstance(subjects.get(vendor_s), dict) else {}
            for err in raw_errors:
                err_s = str(err or "").strip()
                if not err_s:
                    continue
                row = {
                    "vendor_code": vendor_s,
                    "error": err_s,
                    "updated_at": updated,
                    "subject_id": subject.get("id"),
                    "subject": subject.get("name"),
                }
                key = (vendor_s, err_s)
                old = latest.get(key)
                if old is None or (_parse_dt(updated) or datetime.min.replace(tzinfo=timezone.utc)) > (_parse_dt(old.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc)):
                    latest[key] = row
    return list(latest.values())


def _simple_problem_rows(key: str, data: Any) -> list[Any]:
    if not isinstance(data, dict):
        return data if isinstance(data, list) else []
    if key == "banned_products":
        for candidate in (data.get("report"), (data.get("data") or {}).get("report") if isinstance(data.get("data"), dict) else None):
            if isinstance(candidate, list):
                return candidate
        return []
    if key == "price_quarantine":
        candidate = data.get("data")
        if isinstance(candidate, list):
            return candidate
        if isinstance(candidate, dict):
            for field in ("items", "products", "report"):
                if isinstance(candidate.get(field), list):
                    return candidate[field]
        return []
    return []


class CardsAgent(BaseAgent):
    name = "cards"

    async def _load_catalog(self, out: AgentResult) -> tuple[dict[str, Any], set[str]]:
        page_size = 100
        page_limit = 50
        all_cards: list[dict] = []
        cursor: dict | None = None
        seen_cursors: set[tuple[str, str]] = set()
        last_cursor: dict = {}
        pages_scanned = 0
        reached_end = False
        scan_reason = "page_limit"
        scan_error_type: str | None = None
        for _page in range(page_limit):
            args: dict = {"limit": page_size}
            if cursor is not None:
                args["cursor"] = cursor
            try:
                page = await self.call("wb_cards_list", **args)
            except Exception as exc:
                scan_reason = "read_error"
                scan_error_type = type(exc).__name__
                break
            if not isinstance(page, dict):
                scan_reason = "invalid_response"
                break
            rows = page.get("cards")
            if not isinstance(rows, list):
                scan_reason = "missing_cards"
                break
            pages_scanned += 1
            all_cards.extend(x for x in rows if isinstance(x, dict))
            raw_cursor = page.get("cursor") if isinstance(page.get("cursor"), dict) else {}
            updated = str(raw_cursor.get("updatedAt") or "")
            nm = str(raw_cursor.get("nmID") or raw_cursor.get("nmId") or "")
            last_cursor = raw_cursor
            if len(rows) < page_size:
                reached_end = True
                scan_reason = "end_of_catalog"
                break
            if not updated or not nm or not nm.isdigit():
                scan_reason = "missing_cursor"
                break
            marker = (updated, nm)
            if marker in seen_cursors:
                scan_reason = "repeated_cursor"
                break
            seen_cursors.add(marker)
            cursor = {"limit": page_size, "updatedAt": updated, "nmID": int(nm)}

        by_nm: dict[str, dict] = {}
        active_vendor_codes: set[str] = set()
        rows_without_nm_id = 0
        duplicate_nm_ids = 0
        for row in all_cards:
            nm = row.get("nmID", row.get("nmId"))
            if nm is not None and str(nm).strip():
                nm_key = str(nm).strip()
                if nm_key in by_nm:
                    duplicate_nm_ids += 1
                by_nm[nm_key] = row
            else:
                rows_without_nm_id += 1
            vendor = str(row.get("vendorCode") or row.get("supplierArticle") or "").strip()
            if vendor:
                active_vendor_codes.add(vendor)
        complete = reached_end and rows_without_nm_id == 0 and duplicate_nm_ids == 0
        if reached_end and rows_without_nm_id:
            scan_reason = "rows_without_nm_id"
        elif reached_end and duplicate_nm_ids:
            scan_reason = "duplicate_nm_id"
        reasons = {
            "end_of_catalog": f"WB вернул страницу короче {page_size} строк; конец каталога подтверждён.",
            "page_limit": f"Достигнут предел обхода {page_limit} страниц; полнота каталога не подтверждена.",
            "repeated_cursor": "WB повторил курсор страницы; полнота каталога не подтверждена.",
            "missing_cursor": "WB вернул полную страницу без пригодного курсора продолжения; полнота каталога не подтверждена.",
            "invalid_response": "WB вернул ответ неожиданного формата; полнота каталога не подтверждена.",
            "missing_cards": "В ответе WB нет списка карточек; полнота каталога не подтверждена.",
            "read_error": f"Чтение оборвалось после {pages_scanned} страниц из-за ошибки {scan_error_type or 'WB API'}; уже полученные строки сохранены.",
            "rows_without_nm_id": f"Конец каталога подтверждён, но {rows_without_nm_id} строк не содержат nmID и не могут быть надёжно сопоставлены.",
            "duplicate_nm_id": f"Конец каталога подтверждён, но в ответах повторяются {duplicate_nm_ids} nmID; уникальный список может быть неполным.",
        }
        scan_note = reasons.get(scan_reason, "Полнота каталога не подтверждена.")
        payload = {
            "cards": list(by_nm.values()),
            "count": len(by_nm),
            "source_rows": len(all_cards),
            "rows_without_nm_id": rows_without_nm_id,
            "duplicate_nm_ids": duplicate_nm_ids,
            "complete": complete,
            "scan_reason": scan_reason,
            "scan_note": scan_note,
            "scan_error_type": scan_error_type,
            "pages_scanned": pages_scanned,
            "page_size": page_size,
            "page_limit": page_limit,
            "cursor": last_cursor,
            "source": "live_wb_cards",
        }
        out.snapshots.append(("card_catalog", payload))
        if not complete:
            out.events.append(self.event(
                "warning", "card_catalog_partial", "Обход карточек WB не подтверждён как полный",
                f"Получено уникальных карточек: {len(by_nm)}. {scan_note}",
                {"count": len(by_nm), "pages_scanned": pages_scanned, "scan_reason": scan_reason},
            ))
        return payload, active_vendor_codes

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        active_vendor_codes: set[str] = set()
        try:
            _, active_vendor_codes = await self._load_catalog(out)
        except Exception as e:
            out.events.append(self.event("warning", "card_catalog_unavailable", "Каталог карточек WB не удалось прочитать полностью", str(e)))

        checks = [
            ("wb_card_errors", "card_errors", "critical", "Ошибки карточек, требующие действия"),
            ("wb_banned_products", "banned_products", "critical", "Заблокированные или скрытые товары"),
            ("wb_prices_quarantine", "price_quarantine", "critical", "Товары в ценовом карантине"),
        ]
        for tool, key, sev, title in checks:
            try:
                data = await self.call(tool)
            except Exception as e:
                out.events.append(self.event("warning", f"{key}_check_failed", f"Не удалось проверить: {title}", str(e)))
                continue
            out.snapshots.append((key, data if isinstance(data, dict) else {"data": data}))

            if key == "card_errors":
                rows = _card_error_rows(data)
                now = datetime.now(timezone.utc)
                cutoff = now - timedelta(days=14)
                current = []
                historical = []
                for row in rows:
                    dt = _parse_dt(row.get("updated_at"))
                    active = not active_vendor_codes or row.get("vendor_code") in active_vendor_codes
                    fresh = dt is not None and dt >= cutoff
                    if active and fresh:
                        current.append(row)
                    else:
                        historical.append(row)
                if current:
                    articles = ", ".join(x["vendor_code"] for x in current[:3])
                    suffix = f" и ещё {len(current)-3}" if len(current) > 3 else ""
                    out.events.append(self.event(
                        sev,
                        key,
                        title,
                        f"{len(current)} актуальн. ошибок: {articles}{suffix}. Открой сигнал — там конкретная карточка и что исправить.",
                        {"count": len(current), "items": current, "historical_count": len(historical)},
                    ))
                continue

            rows = _simple_problem_rows(key, data)
            if rows:
                out.events.append(self.event(
                    sev,
                    key,
                    title,
                    f"Найдено актуальных элементов: {len(rows)}.",
                    {"count": len(rows), "items": rows[:100]},
                ))
        return out
